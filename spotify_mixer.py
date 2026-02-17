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
        """ Determines if track metadata needs to be fetched from API. """
        val = str(setting).lower()
        if val == 'true': return True
        if val == 'false': return False
        if val == 'auto': return is_scraper 
        return False

    def hydrate_tracks_smart(self, uris):
        if not uris: return []
        temp_tracks = []
        # Batch fetch track details
        for i in range(0, len(uris), 50):
            batch = uris[i:i+50]
            batch_ids = [u.split(':')[-1] for u in batch]
            try:
                res = self.sp_user.tracks(batch_ids, market="from_token")
                for t in res['tracks']:
                    if t and t.get('name'): temp_tracks.append(t)
            except Exception as e:
                print(f"      ! Metadata fetch error: {str(e)[:100]}")
            
        print(f"      -> Metadata fetched for {len(temp_tracks)} tracks. Refreshing via search to fix linking...")
        valid_tracks = []
        for idx, t in enumerate(temp_tracks):
            query = f"track:{t['name']} artist:{t['artists'][0]['name']}"
            try:
                search_res = self.sp_user.search(q=query, type='track', limit=1)
                if search_res['tracks']['items']:
                    found_track = search_res['tracks']['items'][0]
                    # Preserve external IDs (ISRC)
                    if 'external_ids' in t and 'external_ids' not in found_track:
                        found_track['external_ids'] = t['external_ids']
                    valid_tracks.append(found_track)
                else: valid_tracks.append(t)
            except: valid_tracks.append(t)
            
            # Rate limiting protection
            if (idx + 1) % 50 == 0: time.sleep(0.05)
        return valid_tracks

    def get_tracks_from_file(self, filename, hydrate='auto'):
        tracks = []
        try:
            file_path = filename if os.path.isabs(filename) else os.path.join(self.script_dir, filename)
            
            # --- JSON DB SUPPORT ---
            if file_path.endswith('.json'):
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        # Expects: { "tracks": [...] }
                        tracks = data.get('tracks', [])
                        print(f"    > Database '{filename}': {len(tracks)} items loaded.")
                        return tracks
                else:
                    print(f"    ! Database '{filename}' does not exist yet (will be created on sync).")
                    return []

            # --- LEGACY (.TXT) SUPPORT ---
            with open(file_path, 'r', encoding='utf-8') as f:
                uris = [line.strip() for line in f if line.strip().startswith('spotify:track:')]
            
            print(f"    > File '{filename}': {len(uris)} URIs found.")
            uris = list(set(uris))
            
            if self._should_hydrate(hydrate, is_scraper=False):
                tracks = self.hydrate_tracks_smart(uris)
            else:
                tracks = [{'uri': u, 'id': u.split(':')[-1]} for u in uris]
        except Exception as e: 
            print(f"    ! Error reading file: {e}")
        return tracks

    def scrape_playlist_tracks(self, playlist_id, hydrate='auto'):
        """ Fallback mechanism: Scrapes Spotify Embed if API fails (e.g. 404/403) """
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
                    print(f"      -> FOUND! {len(ids)} track IDs in HTML.")
                    uris = [f"spotify:track:{tid}" for tid in ids]
                    if self._should_hydrate(hydrate, is_scraper=True):
                        print("      -> Auto-Hydrate active for Scraper results.")
                        return self.hydrate_tracks_smart(uris)
                    else:
                        return [{'uri': u, 'id': u.split(':')[-1]} for u in uris]
                else: print("      ! No tracks found in HTML code.")
            else: print(f"      ! Could not load page (Status: {response.status_code})")
        except Exception as e: print(f"      ! Scraper error: {e}")
        return []

    def search_playlist_fallback(self, playlist_id, playlist_name=None, hydrate='auto'):
        if not playlist_name: return []
        is_spotify_owned = str(playlist_id).startswith("37i")
        final_query = f'"{playlist_name}" owner:spotify' if is_spotify_owned else playlist_name
        print(f"    > Fallback: Searching for '{playlist_name}' on Spotify...")
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
            if playlist_id == 'me':
                results = self.sp_user.current_user_saved_tracks(limit=50, market="from_token")
                for item in results['items']: tracks.append(item['track'])
                return tracks
            try:
                # Try Private/User API first
                results = self.sp_user.playlist_items(playlist_id, market="from_token")
                fetch_client = self.sp_user
            except:
                # Fallback to Public API or Scraper
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

    # --- LOCAL DATABASE SYNC ---
    def sync_local_db(self, playlist_id, db_filename, mode='append', store_type='tracks', clear_source=False):
        if not os.path.isabs(db_filename):
            db_path = os.path.join(self.script_dir, db_filename)
        else: db_path = db_filename

        print(f"  - Syncing playlist {playlist_id} to DB '{db_filename}' (mode: {mode}, type: {store_type})...")
        
        # 1. Fetch tracks (Hydrate needed for artist info)
        spotify_items = self.get_tracks(playlist_id, hydrate=True)
        if not spotify_items:
            print("    > No items found in source.")
            return []

        # 2. Load DB
        current_db = {'tracks': []}
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f: current_db = json.load(f)
            except: pass
        
        db_list = current_db.get('tracks', [])
        
        # 3. Collect Input IDs
        input_ids = set()
        for t in spotify_items:
            if store_type == 'tracks': input_ids.add(t['id'])
            elif store_type == 'artists':
                for a in t.get('artists', []): input_ids.add(a['id'])

        count = 0
        if mode == 'append':
            # Append unique items
            existing_ids = {item['id'] for item in db_list}
            for t in spotify_items:
                if store_type == 'tracks':
                    if t['id'] not in existing_ids:
                        entry = {
                            'id': t['id'], 'uri': t['uri'], 'name': t['name'],
                            'artists': [{'name': a['name'], 'id': a['id']} for a in t.get('artists', [])]
                        }
                        db_list.append(entry); existing_ids.add(t['id']); count += 1
                elif store_type == 'artists':
                    for a in t.get('artists', []):
                        if a['id'] not in existing_ids:
                            # Save in a structure compatible with filter_artist
                            entry = {'id': a['id'], 'name': a['name'], 'artists': [{'id': a['id']}]}
                            db_list.append(entry); existing_ids.add(a['id']); count += 1
            print(f"    > {count} items added.")

        elif mode == 'remove':
            # Remove items from DB
            orig_len = len(db_list)
            db_list = [item for item in db_list if item['id'] not in input_ids]
            count = orig_len - len(db_list)
            print(f"    > {count} items removed.")

        # 4. Save
        current_db['tracks'] = db_list
        if count > 0:
            with open(db_path, 'w', encoding='utf-8') as f: json.dump(current_db, f, indent=2)
            print("    > Database updated.")
        else: print("    > No changes.")

        # 5. Clear playlist
        if clear_source and spotify_items:
            try: self.sp_user.playlist_replace_items(playlist_id, [])
            except: print("    ! Could not clear playlist.")
            
        return db_list

    def get_audio_features_reccobeats(self, track_ids):
        """ Fetch BPM/Energy from ReccoBeats (fallback for Spotify 2026 API changes) """
        base_url = "https://api.reccobeats.com/v1/track" 
        results = []
        total = len(track_ids)
        for i in range(0, total, 30):
            batch = track_ids[i:i+30]
            ids_str = ",".join(batch)
            try:
                resp = requests.get(f"{base_url}?ids={ids_str}", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get('content', []) if isinstance(data, dict) else data
                    results.extend(items)
            except: pass
            time.sleep(1.0) 
        return results

    def _apply_audio_features(self, tracks, features_list, min_bpm, max_bpm, min_energy, max_energy):
        valid_tracks = []
        isrc_map = {}
        # Map ISRC to track objects for matching
        for t in tracks:
            isrc = t.get('external_ids', {}).get('isrc')
            if isrc:
                if isrc not in isrc_map: isrc_map[isrc] = []
                isrc_map[isrc].append(t)
        
        # Apply features
        for f in features_list:
            f_isrc = f.get('isrc')
            if f_isrc and f_isrc in isrc_map:
                try:
                    bpm, energy = float(f.get('tempo', 0)), float(f.get('energy', 0))
                    for t in isrc_map[f_isrc]:
                        t['_audio_found'] = True
                        if (min_bpm <= bpm <= max_bpm) and (min_energy <= energy <= max_energy):
                            t['bpm'], t['energy'] = bpm, energy
                            if t not in valid_tracks: valid_tracks.append(t)
                except: pass
            # Fallback mapping via ID if ISRC missing
            elif 'href' in f and 'spotify.com/track/' in f['href']:
                 try:
                     s_id = f['href'].split('track/')[-1].split('?')[0]
                     for t in tracks:
                         if t['id'] == s_id:
                             t['_audio_found'] = True
                             bpm, energy = float(f.get('tempo', 0)), float(f.get('energy', 0))
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
            if 'comment' in step:
                print(f"\n[{step['comment']}]")
                if not action: continue

            print(f"> Action: {action.upper()} -> {output_name}")
            result = []

            if action == 'source':
                h = step.get('hydrate', 'auto')
                result = self.get_tracks(step['id'], step.get('name'), hydrate=h)
                print(f"  - Fetched: {len(result)} tracks (Hydrate: {h})")

            elif action == 'source_file':
                h = step.get('hydrate', 'auto')
                result = self.get_tracks_from_file(step['filename'], hydrate=h)
                print(f"  - File: {len(result)} items (Hydrate: {h})")

            elif action == 'sync_local_db':
                pl_id = step['id']
                db_file = step['filename']
                mode = step.get('mode', 'append')
                store_type = step.get('store_type', 'tracks')
                clear = step.get('clear_source', True)
                result = self.sync_local_db(pl_id, db_file, mode, store_type, clear)

            elif action == 'slice':
                inp = self.resolve_input(step['input']); limit = step['amount']
                result = inp[:limit]
                print(f"  - First {limit} taken.")

            elif action == 'sample':
                inp = self.resolve_input(step['input']); count = step['amount']
                result = random.sample(inp, count) if len(inp) > count else inp
                print(f"  - Sampled {len(result)} tracks.")

            elif action == 'mix':
                for name in step['inputs']: result.extend(self.memory.get(name, []))
                random.shuffle(result)
                print(f"  - Mixed: {len(result)} tracks.")

            elif action == 'inject':
                base = self.resolve_input(step['input'])[:]; to_inject = self.resolve_input(step['inject_input'])[:]
                interval = step.get('every', 10); variance = step.get('variance', 4)
                print(f"  - Injecting from '{step['inject_input']}' every ~{interval} tracks.")
                random.shuffle(to_inject); final_list = []; idx_base = 0
                while idx_base < len(base):
                    current_interval = max(1, interval + random.randint(-variance, variance))
                    chunk = base[idx_base : idx_base + current_interval]
                    final_list.extend(chunk); idx_base += current_interval
                    if to_inject and idx_base < len(base): final_list.append(to_inject.pop(0))
                result = final_list
                print(f"  - Injection complete. Total: {len(result)} tracks.")

            elif action == 'dedup':
                inp = self.resolve_input(step['input']); seen = set()
                for t in inp:
                    if t['uri'] not in seen: result.append(t); seen.add(t['uri'])
                print(f"  - Dedup: {len(result)} remaining.")

            elif action == 'filter_exclude':
                inp = self.resolve_input(step['input']); exclude_list = self.resolve_input(step['exclude_input'])
                ban_uris = {t['uri'] for t in exclude_list}
                result = [t for t in inp if t['uri'] not in ban_uris]
                print(f"  - Exclude Filter: {len(inp) - len(result)} removed.")

            elif action == 'filter_artist':
                inp = self.resolve_input(step['input']); blacklist = self.resolve_input(step['blacklist_input'])
                bad_ids = set()
                # Collect IDs from both track lists and artist-only DBs
                for item in blacklist:
                    for a in item.get('artists', []): bad_ids.add(a['id'])
                    if 'id' in item and len(item.get('artists', [])) == 1: bad_ids.add(item['id'])
                
                result = [t for t in inp if not (set(a['id'] for a in t.get('artists', [])) & bad_ids)]
                print(f"  - Artist Filter: {len(inp) - len(result)} removed.")

            elif action == 'filter_genre':
                inp = self.resolve_input(step['input'])
                target_genres = [g.lower() for g in step['genres']]
                mode = step.get('mode', 'include')
                print(f"  - Genre Filter ({mode})...")
                artist_ids = list({a['id'] for t in inp for a in t.get('artists', [])})
                artist_genres = {}
                for i in range(0, len(artist_ids), 50):
                    batch = artist_ids[i:i+50]
                    try:
                        infos = self.sp_user.artists(batch)
                        for artist in infos['artists']:
                            artist_genres[artist['id']] = [g.lower() for g in artist['genres']]
                    except Exception: pass
                for t in inp:
                    track_genres = set()
                    for a in t.get('artists', []):
                        track_genres.update(artist_genres.get(a['id'], []))
                    match = False
                    for g in track_genres:
                        for target in target_genres:
                            if target in g: match = True; break
                        if match: break
                    if mode == 'include' and match: result.append(t)
                    elif mode == 'exclude' and not match: result.append(t)
                print(f"  - Genre Filter: {len(result)} remaining.")

            elif action == 'season':
                month = datetime.now().month
                print(f"  - Date check: {datetime.now().strftime('%Y-%m-%d')} (Month: {month})")
                cases = step.get('cases', []); active_season = False
                for case in cases:
                    if month in case['months']:
                        print(f"      -> MATCH! Season '{case['name']}' is active.")
                        active_season = True
                        for src in case['sources']:
                            print(f"      > Fetching source: {src}")
                            tracks = self.get_tracks(src, hydrate='auto') 
                            result.extend(tracks)
                        break 
                if not active_season: result = [] 
                if active_season and step.get('sample'):
                     limit = step['sample']
                     if len(result) > limit:
                         result = random.sample(result, limit)
                         print(f"  - Sampled {len(result)} tracks.")

            elif action == 'filter_audio':
                inp = self.resolve_input(step['input'])
                min_bpm = step.get('min_bpm', 0); max_bpm = step.get('max_bpm', 999)
                min_energy = step.get('min_energy', 0.0); max_energy = step.get('max_energy', 1.0)
                print(f"  - Audio Analysis on {len(inp)} tracks...")
                track_ids = [t['id'] for t in inp if t.get('id')]
                valid_tracks = []
                
                # 1. Try ReccoBeats
                print(f"    > Trying ReccoBeats API...")
                features_list = self.get_audio_features_reccobeats(track_ids)
                valid_tracks = self._apply_audio_features(inp, features_list, min_bpm, max_bpm, min_energy, max_energy)
                
                # 2. Try Spotify Fallback (if enabled and tracks remaining)
                remaining = [t for t in inp if not t.get('_audio_found')]
                if remaining and not self.spotify_features_disabled:
                    print(f"    > {len(remaining)} tracks remaining. Spotify Fallback...")
                    track_ids_sp = [t['id'] for t in remaining]
                    for i in range(0, len(track_ids_sp), 100):
                        if self.spotify_features_disabled: break
                        try:
                            features = self.sp_user.audio_features(track_ids_sp[i:i+100])
                            for j, feat in enumerate(features):
                                if feat:
                                    bpm, energy = feat['tempo'], feat['energy']
                                    if (min_bpm <= bpm <= max_bpm) and (min_energy <= energy <= max_energy):
                                        t = remaining[i+j]; t['bpm'], t['energy'] = bpm, energy
                                        valid_tracks.append(t)
                            time.sleep(0.1) 
                        except Exception as e:
                            if "403" in str(e):
                                print("    ! Spotify API 403 Forbidden. Disabling Audio Features.")
                                self.spotify_features_disabled = True
                        except: pass
                
                if not valid_tracks and inp and step.get('fallback') != 'none': 
                    result = inp
                    print("    ! Filter 0 results. Fallback active (keeping all).")
                else: 
                    result = valid_tracks
                print(f"  - Audio Filter: {len(result)} remaining.")

            elif action == 'weighted_shuffle':
                inp = self.resolve_input(step['input']); attr = step.get('by', 'popularity'); factor = step.get('factor', 50)
                reverse = step.get('reverse', True)
                sorted_list = sorted(inp, key=lambda t: t.get(attr, 0), reverse=reverse)
                shuffled = []
                for index, track in enumerate(sorted_list):
                    shuffled.append((track, index + random.uniform(-factor, factor)))
                shuffled.sort(key=lambda x: x[1])
                result = [x[0] for x in shuffled]

            elif action == 'artist_separation':
                inp = self.resolve_input(step['input']); min_dist = step.get('min_distance', 3)
                pool = inp[:]; random.shuffle(pool); result = []; postponed = []
                while pool:
                    track = pool.pop(0); track_artists = {a['id'] for a in track.get('artists', [])}
                    conflict = False
                    if track_artists:
                        check_range = result[-min_dist:] if len(result) >= min_dist else result
                        for placed in check_range:
                            if track_artists & {a['id'] for a in placed.get('artists', [])}: conflict = True; break
                    if conflict: postponed.append(track)
                    else:
                        result.append(track); 
                        if postponed: pool.insert(0, postponed.pop(0))
                result.extend(postponed)
                print(f"  - Separation ready. Length: {len(result)}")

            elif action == 'sort':
                inp = self.resolve_input(step['input']); attr = step.get('by', 'popularity'); reverse = step.get('reverse', True)
                result = sorted(inp, key=lambda t: t.get(attr, 0), reverse=reverse)

            elif action == 'save':
                inp = self.resolve_input(step['input']); target_id = step['id']
                if step.get('shuffle', False): random.shuffle(inp)
                uris = [t['uri'] for t in inp]
                try:
                    print(f"  - Saving to playlist {target_id}...")
                    if uris:
                        # Wipe & Write
                        self.sp_user.playlist_replace_items(target_id, [])
                        time.sleep(0.5) 
                        for i in range(0, len(uris), 100):
                            self.sp_user.playlist_add_items(target_id, uris[i:i+100])
                        print(f"  > SAVED: {len(uris)} tracks.")
                    else: print("  ! Empty list. Nothing saved.")
                except Exception as e: print(f"  ! SAVE ERROR: {e}")
                result = inp

            self.memory[output_name] = result

if __name__ == "__main__":
    if len(sys.argv) < 2: 
        print("Usage: python spotify_mixer.py config.json")
    else: 
        SpotifyMixer(sys.argv[1]).run()