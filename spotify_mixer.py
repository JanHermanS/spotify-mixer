import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
import os
import random
import json
import sys
import time
from datetime import datetime
import requests
import re

class SpotifyMixer:
    def __init__(self, config_file):
        # Determine script location for relative paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.load_config(config_file)
        self.memory = {} 
        self.spotify_features_disabled = False 
        
        print(f"--- Spotify Mixer Initialization ---")
        print(f"  - Working directory: {self.script_dir}")
        self.sp_user = self.authenticate_user()
        self.sp_public = self.authenticate_public()
        print("  - Connected to Spotify API.")

    def load_config(self, filename):
        if not os.path.isabs(filename):
            filename = os.path.join(self.script_dir, filename)
        with open(filename, 'r') as f:
            self.config = json.load(f)

    def authenticate_user(self):
        creds = self.config['credentials']
        os.chdir(self.script_dir)
        cache_path = os.path.join(self.script_dir, ".cache")
        
        if not os.access(self.script_dir, os.W_OK):
            print(f"  ! WARNING: No write permissions in {self.script_dir}")

        auth_manager = SpotifyOAuth(
            client_id=creds['client_id'],
            client_secret=creds['client_secret'],
            redirect_uri=creds['redirect_uri'],
            scope="playlist-modify-public playlist-modify-private playlist-read-private user-library-read",
            cache_path=cache_path,
            open_browser=False
        )

        token_info = auth_manager.get_cached_token()
        if not token_info:
            print("\n" + "="*60)
            print("AUTHENTICATION REQUIRED")
            print("Copy the URL below to your browser, log in, and paste")
            print("the redirect URL (containing 'code=...') below.")
            print("="*60 + "\n")
        
        return spotipy.Spotify(auth_manager=auth_manager)

    def authenticate_public(self):
        creds = self.config['credentials']
        return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=creds['client_id'],
            client_secret=creds['client_secret']
        ))

    def resolve_input(self, input_name):
        if isinstance(input_name, list):
            combined = []
            for name in input_name:
                combined.extend(self.memory.get(name, []))
            return combined
        return self.memory.get(input_name, [])

    # --- HYDRATE LOGIC ---
    def _should_hydrate(self, setting, is_scraper=False):
        val = str(setting).lower()
        if val == 'true': return True
        if val == 'false': return False
        if val == 'auto': return is_scraper 
        return False

    def hydrate_tracks_smart(self, uris):
        if not uris: return []
        temp_tracks = []
        for i in range(0, len(uris), 50):
            batch = uris[i:i+50]
            batch_ids = [u.split(':')[-1] for u in batch]
            try:
                res = self.sp_user.tracks(batch_ids, market="from_token")
                for t in res['tracks']:
                    if t and t.get('name'): temp_tracks.append(t)
            except Exception as e:
                print(f"      ! Metadata fetch error: {str(e)[:100]}")
            
        print(f"      -> Metadata fetched for {len(temp_tracks)} tracks. Refreshing via search...")
        valid_tracks = []
        for idx, t in enumerate(temp_tracks):
            query = f"track:{t['name']} artist:{t['artists'][0]['name']}"
            try:
                search_res = self.sp_user.search(q=query, type='track', limit=1)
                if search_res['tracks']['items']:
                    found_track = search_res['tracks']['items'][0]
                    if 'external_ids' in t and 'external_ids' not in found_track:
                        found_track['external_ids'] = t['external_ids']
                    valid_tracks.append(found_track)
                else: valid_tracks.append(t)
            except: valid_tracks.append(t)
            if (idx + 1) % 50 == 0: time.sleep(0.05)
        return valid_tracks

    def get_tracks_from_file(self, filename, hydrate='auto'):
        tracks = []
        try:
            file_path = filename if os.path.isabs(filename) else os.path.join(self.script_dir, filename)
            if file_path.endswith('.json'):
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        tracks = data.get('tracks', [])
                        print(f"    > Database '{filename}': {len(tracks)} items loaded.")
                        return tracks
                else:
                    print(f"    ! Database '{filename}' does not exist yet.")
                    return []

            with open(file_path, 'r', encoding='utf-8') as f:
                uris = [line.strip() for line in f if line.strip().startswith('spotify:track:')]
            
            print(f"    > File '{filename}': {len(uris)} URIs found.")
            uris = list(set(uris))
            
            if self._should_hydrate(hydrate, is_scraper=False):
                tracks = self.hydrate_tracks_smart(uris)
            else:
                tracks = [{'uri': u, 'id': u.split(':')[-1]} for u in uris]
        except Exception as e: print(f"    ! Error reading file: {e}")
        return tracks

    def scrape_playlist_tracks(self, playlist_id, hydrate='auto'):
        url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
        print(f"    > Scraper: Deep-scan on Embed page ({url})...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                html = response.text
                ids = re.findall(r'spotify:track:([a-zA-Z0-9]{22})', html)
                ids = list(dict.fromkeys(ids))
                if ids:
                    print(f"      -> FOUND! {len(ids)} track IDs.")
                    uris = [f"spotify:track:{tid}" for tid in ids]
                    if self._should_hydrate(hydrate, is_scraper=True):
                        return self.hydrate_tracks_smart(uris)
                    else:
                        return [{'uri': u, 'id': u.split(':')[-1]} for u in uris]
            else: print(f"      ! Could not load page (Status: {response.status_code})")
        except Exception as e: print(f"      ! Scraper error: {e}")
        return []

    def search_playlist_fallback(self, playlist_id, playlist_name=None, hydrate='auto'):
        if not playlist_name: return []
        is_spotify_owned = str(playlist_id).startswith("37i")
        final_query = f'"{playlist_name}" owner:spotify' if is_spotify_owned else playlist_name
        print(f"    > Fallback: Searching for '{playlist_name}'...")
        try:
            results = self.sp_user.search(q=final_query, type='playlist', limit=5)
            if results and 'playlists' in results and results['playlists']['items']:
                for item in results['playlists']['items']:
                    if not item: continue
                    return self.get_tracks_simple(item['id'], hydrate=hydrate)
        except Exception as e: print(f"    ! Search error: {e}")
        return []

    def get_tracks_simple(self, playlist_id, hydrate='auto'):
        tracks = []
        try:
            results = self.sp_user.playlist_items(playlist_id, market="from_token")
            while results:
                for item in results['items']:
                    if item.get('track') and item['track'].get('uri'): tracks.append(item['track'])
                if results['next']: results = self.sp_user.next(results)
                else: break
            if self._should_hydrate(hydrate, is_scraper=False) and tracks:
                 uris = [t['uri'] for t in tracks]
                 return self.hydrate_tracks_smart(uris)
            return tracks
        except: return self.scrape_playlist_tracks(playlist_id, hydrate=hydrate)

    def get_tracks(self, playlist_id, playlist_name=None, hydrate='auto'):
        if "spotify.com" in playlist_id: playlist_id = playlist_id.split("/")[-1].split("?")[0]
        tracks = []
        should_hydrate = self._should_hydrate(hydrate, is_scraper=False)

        try:
            # --- TOP TRACKS SUPPORT ---
            if str(playlist_id).startswith("top_tracks"):
                time_range = 'medium_term'
                if 'short' in str(playlist_id): time_range = 'short_term'
                elif 'long' in str(playlist_id): time_range = 'long_term'
                print(f"    > Fetching Top Tracks ({time_range})...")
                results = self.sp_user.current_user_top_tracks(limit=50, time_range=time_range)
                return results['items']

            if playlist_id == 'me':
                results = self.sp_user.current_user_saved_tracks(limit=50, market="from_token")
                for item in results['items']: tracks.append(item['track'])
                return tracks
            
            try:
                results = self.sp_user.playlist_items(playlist_id, market="from_token")
                fetch_client = self.sp_user
            except:
                if str(playlist_id).startswith("37i"): return self.scrape_playlist_tracks(playlist_id, hydrate=hydrate)
                try:
                    results = self.sp_public.playlist_items(playlist_id, market="NL")
                    fetch_client = self.sp_public
                except: return self.scrape_playlist_tracks(playlist_id, hydrate=hydrate)

            if results:
                while results:
                    for item in results['items']:
                        if item.get('track') and item['track'].get('id'): tracks.append(item['track'])
                    if results['next']: results = fetch_client.next(results)
                    else: break
        except Exception:
            return self.search_playlist_fallback(playlist_id, playlist_name, hydrate=hydrate)

        if should_hydrate and tracks:
            print(f"    > Forced Hydrating {len(tracks)} tracks via Re-Search...")
            return self.hydrate_tracks_smart([t['uri'] for t in tracks])
        return tracks

    def sync_local_db(self, playlist_id, db_filename, mode='append', store_type='tracks', clear_source=False):
        if not os.path.isabs(db_filename): db_path = os.path.join(self.script_dir, db_filename)
        else: db_path = db_filename
        print(f"  - Syncing playlist {playlist_id} to DB '{db_filename}'...")
        
        spotify_items = self.get_tracks(playlist_id, hydrate=True)
        if not spotify_items: return []

        current_db = {'tracks': []}
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f: current_db = json.load(f)
            except: pass
        
        db_list = current_db.get('tracks', [])
        input_ids = set()
        for t in spotify_items:
            if store_type == 'tracks': input_ids.add(t['id'])
            elif store_type == 'artists':
                for a in t.get('artists', []): input_ids.add(a['id'])

        count = 0
        if mode == 'append':
            existing_ids = {item['id'] for item in db_list}
            for t in spotify_items:
                if store_type == 'tracks' and t['id'] not in existing_ids:
                    entry = {'id': t['id'], 'uri': t['uri'], 'name': t['name'], 'artists': [{'name': a['name'], 'id': a['id']} for a in t.get('artists', [])]}
                    db_list.append(entry); existing_ids.add(t['id']); count += 1
                elif store_type == 'artists':
                    for a in t.get('artists', []):
                        if a['id'] not in existing_ids:
                            entry = {'id': a['id'], 'name': a['name'], 'artists': [{'id': a['id']}]}
                            db_list.append(entry); existing_ids.add(a['id']); count += 1
            print(f"    > {count} items added.")

        elif mode == 'remove':
            orig_len = len(db_list)
            db_list = [item for item in db_list if item['id'] not in input_ids]
            count = orig_len - len(db_list)
            print(f"    > {count} items removed.")

        current_db['tracks'] = db_list
        if count > 0:
            with open(db_path, 'w', encoding='utf-8') as f: json.dump(current_db, f, indent=2)
        
        if clear_source and spotify_items:
            try: self.sp_user.playlist_replace_items(playlist_id, [])
            except: print("    ! Could not clear playlist.")
        return db_list

    def get_audio_features_reccobeats(self, track_ids):
        base_url = "https://api.reccobeats.com/v1/track" 
        results = []
        for i in range(0, len(track_ids), 30):
            try:
                resp = requests.get(f"{base_url}?ids={','.join(track_ids[i:i+30])}", timeout=10)
                if resp.status_code == 200: results.extend(resp.json().get('content', []))
            except: pass
            time.sleep(1.0) 
        return results

    def _apply_audio_features(self, tracks, features_list, min_bpm, max_bpm, min_energy, max_energy):
        valid_tracks = []; isrc_map = {}
        for t in tracks:
            if t.get('external_ids', {}).get('isrc'):
                isrc_map.setdefault(t['external_ids']['isrc'], []).append(t)
        
        for f in features_list:
            targets = []
            if f.get('isrc') in isrc_map: targets = isrc_map[f['isrc']]
            elif 'href' in f: # Fallback ID match
                 try:
                     sid = f['href'].split('track/')[-1].split('?')[0]
                     targets = [t for t in tracks if t['id'] == sid]
                 except: pass
            
            for t in targets:
                try:
                    bpm, energy = float(f.get('tempo', 0)), float(f.get('energy', 0))
                    t['_audio_found'] = True
                    if (min_bpm <= bpm <= max_bpm) and (min_energy <= energy <= max_energy):
                        t['bpm'], t['energy'] = bpm, energy
                        if t not in valid_tracks: valid_tracks.append(t)
                except: pass
        return valid_tracks

    def run(self):
        print(f"--- Spotify Mixer Started ---")
        steps = self.config['workflow']
        for step in steps:
            action = step.get('action') 
            output_name = step.get('output', 'temp') 
            if 'comment' in step: print(f"\n[{step['comment']}]")
            if not action: continue

            print(f"> Action: {action.upper()} -> {output_name}")
            result = []

            if action == 'source':
                h = step.get('hydrate', 'auto')
                result = self.get_tracks(step['id'], step.get('name'), hydrate=h)
                print(f"  - Fetched: {len(result)} tracks.")

            elif action == 'source_file':
                result = self.get_tracks_from_file(step['filename'], hydrate=step.get('hydrate', 'auto'))
                print(f"  - File: {len(result)} items.")

            elif action == 'sync_local_db':
                result = self.sync_local_db(step['id'], step['filename'], step.get('mode', 'append'), step.get('store_type', 'tracks'), step.get('clear_source', True))

            elif action == 'slice':
                result = self.resolve_input(step['input'])[:step['amount']]
                print(f"  - Sliced to {len(result)}.")

            elif action == 'sample':
                inp = self.resolve_input(step['input'])
                result = random.sample(inp, step['amount']) if len(inp) > step['amount'] else inp
                print(f"  - Sampled {len(result)}.")

            elif action == 'mix':
                for name in step['inputs']: result.extend(self.memory.get(name, []))
                random.shuffle(result)
                print(f"  - Mixed: {len(result)} tracks.")

            elif action == 'inject':
                base = self.resolve_input(step['input'])[:]; to_inject = self.resolve_input(step['inject_input'])[:]
                interval = step.get('every', 10); variance = step.get('variance', 4)
                random.shuffle(to_inject); final_list = []; idx_base = 0
                while idx_base < len(base):
                    chunk = base[idx_base : idx_base + max(1, interval + random.randint(-variance, variance))]
                    final_list.extend(chunk); idx_base += len(chunk)
                    if to_inject and idx_base < len(base): final_list.append(to_inject.pop(0))
                result = final_list
                print(f"  - Injected. Total: {len(result)}.")

            elif action == 'dedup':
                seen = set(); result = [t for t in self.resolve_input(step['input']) if not (t['uri'] in seen or seen.add(t['uri']))]
                print(f"  - Dedup: {len(result)} left.")

            elif action == 'filter_exclude':
                ban_uris = {t['uri'] for t in self.resolve_input(step['exclude_input'])}
                result = [t for t in self.resolve_input(step['input']) if t['uri'] not in ban_uris]
                print(f"  - Exclude: {len(result)} left.")

            elif action == 'filter_artist':
                blacklist = self.resolve_input(step['blacklist_input'])
                bad_ids = {a['id'] for item in blacklist for a in item.get('artists', [])} | {item['id'] for item in blacklist if 'id' in item and len(item.get('artists', []))==1}
                result = [t for t in self.resolve_input(step['input']) if not (set(a['id'] for a in t.get('artists', [])) & bad_ids)]
                print(f"  - Artist Filter: {len(result)} left.")

            elif action == 'filter_genre':
                inp = self.resolve_input(step['input']); target = [g.lower() for g in step['genres']]
                # Quick batch fetch artist genres
                a_ids = list({a['id'] for t in inp for a in t.get('artists', [])}); a_map = {}
                for i in range(0, len(a_ids), 50):
                    try: 
                        for a in self.sp_user.artists(a_ids[i:i+50])['artists']: a_map[a['id']] = [x.lower() for x in a['genres']]
                    except: pass
                
                for t in inp:
                    t_genres = set(g for a in t.get('artists', []) for g in a_map.get(a['id'], []))
                    match = any(tg for tg in t_genres for k in target if k in tg)
                    if (step.get('mode', 'include') == 'include' and match) or (step.get('mode') == 'exclude' and not match): result.append(t)
                print(f"  - Genre Filter: {len(result)} left.")

            elif action == 'filter_audio':
                inp = self.resolve_input(step['input'])
                print(f"  - Audio Analysis on {len(inp)} tracks...")
                valid = self._apply_audio_features(inp, self.get_audio_features_reccobeats([t['id'] for t in inp]), step.get('min_bpm',0), step.get('max_bpm',999), step.get('min_energy',0), step.get('max_energy',1))
                
                # Spotify Fallback
                remaining = [t for t in inp if not t.get('_audio_found')]
                if remaining and not self.spotify_features_disabled:
                    print(f"    > {len(remaining)} tracks via Spotify API...")
                    for i in range(0, len(remaining), 100):
                        if self.spotify_features_disabled: break
                        try:
                            feats = self.sp_user.audio_features([t['id'] for t in remaining[i:i+100]])
                            for j, f in enumerate(feats):
                                if f and (step.get('min_bpm',0) <= f['tempo'] <= step.get('max_bpm',999)) and (step.get('min_energy',0) <= f['energy'] <= step.get('max_energy',1)):
                                    t = remaining[i+j]; t['bpm'], t['energy'] = f['tempo'], f['energy']; valid.append(t)
                        except Exception as e: 
                            if "403" in str(e): self.spotify_features_disabled = True; print("    ! Spotify API 403. Disabled.")
                
                result = valid if valid or step.get('fallback') == 'none' else inp
                print(f"  - Audio Filter: {len(result)} left.")

            elif action == 'season':
                m = datetime.now().month
                for c in step.get('cases', []):
                    if m in c['months']:
                        print(f"      -> Season '{c['name']}' active.")
                        for src in c['sources']:
                            result.extend(self.get_tracks(src, hydrate='auto'))
                        break
                
                # FIX: Safe sampling if result < required sample
                if result and step.get('sample'):
                    req = step['sample']
                    if len(result) > req:
                        result = random.sample(result, req)
                        print(f"  - Sampled {req} tracks.")
                    else:
                        print(f"  - Sample requested ({req}) > available ({len(result)}). Keeping all.")

            elif action == 'weighted_shuffle':
                inp = self.resolve_input(step['input']); fac = step.get('factor', 50)
                shuffled = [(t, i + random.uniform(-fac, fac)) for i, t in enumerate(sorted(inp, key=lambda x: x.get(step.get('by', 'popularity'), 0), reverse=True))]
                result = [x[0] for x in sorted(shuffled, key=lambda x: x[1])]

            elif action == 'artist_separation':
                inp = self.resolve_input(step['input']); dist = step.get('min_distance', 3)
                pool = inp[:]; random.shuffle(pool); postponed = []
                while pool:
                    t = pool.pop(0); a_ids = {a['id'] for a in t.get('artists', [])}
                    if any(a_ids & {a['id'] for a in p.get('artists', [])} for p in result[-dist:]): postponed.append(t)
                    else: result.append(t); pool = [postponed.pop(0)] + pool if postponed else pool
                result.extend(postponed)
                print(f"  - Separation done. Length: {len(result)}")

            elif action == 'sort':
                result = sorted(self.resolve_input(step['input']), key=lambda t: t.get(step.get('by', 'popularity'), 0), reverse=step.get('reverse', True))

            elif action == 'save':
                inp = self.resolve_input(step['input'])
                
                # --- AUTO CREATE PLAYLIST ---
                target_id = step.get('id')
                if step.get('create_new', False) or not target_id:
                    name = step.get('name', f"Mixer Output {datetime.now().strftime('%Y-%m-%d')}")
                    desc = step.get('description', "Created by Spotify Mixer")
                    user_id = self.sp_user.current_user()['id']
                    print(f"  - Creating NEW playlist '{name}'...")
                    new_pl = self.sp_user.user_playlist_create(user=user_id, name=name, public=False, description=desc)
                    target_id = new_pl['id']
                    print(f"    > Created! ID: {target_id}")
                # ----------------------------

                if step.get('shuffle', False): random.shuffle(inp)
                uris = [t['uri'] for t in inp]
                try:
                    print(f"  - Saving to {target_id}...")
                    if uris:
                        self.sp_user.playlist_replace_items(target_id, [])
                        time.sleep(0.5) 
                        for i in range(0, len(uris), 100): self.sp_user.playlist_add_items(target_id, uris[i:i+100])
                        print(f"  > SAVED: {len(uris)} tracks.")
                    else: print("  ! Empty list.")
                except Exception as e: print(f"  ! SAVE ERROR: {e}")
                result = inp

            self.memory[output_name] = result

if __name__ == "__main__":
    if len(sys.argv) < 2: print("Usage: python spotify_mixer.py config.json")
    else: SpotifyMixer(sys.argv[1]).run()