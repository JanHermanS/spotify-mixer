# Spotify Mixer 🎧

**Spotify Mixer** is a powerful, JSON-configured automation tool written in Python. It allows you to build complex, dynamic Spotify playlists by mixing sources, filtering by genre/bpm, injecting new tracks, and managing local blacklists.

Unlike simple "shufflers", this tool functions like a radio station programmer: it builds the flow of the playlist, enforces artist separation, and keeps your library fresh.

---

## ⚠️ Important Disclaimers

1. **Spotify Premium Required**: As of recent API changes (Feb 2026), an active **Spotify Premium subscription is mandatory** to use the Spotify Developer API. If your Premium subscription expires, the script will stop working and throw API errors.
2. **App User Limits**: Spotify now restricts Developer Apps to a maximum of 5 registered users. This tool is intended for personal use, so this shouldn't be an issue, but you must explicitly whitelist your own account in the Spotify Developer Dashboard (see Setup).
3. **Experimental Scraper**: This tool includes a fallback mechanism that scrapes the Spotify Embed HTML when the official API fails (e.g., for certain "Spotify Owned" playlists that return 404 via API). **This feature is experimental** and meant for educational purposes.
4. **Spotify API 2026 Audio Features**: Spotify has heavily restricted access to "Audio Features" (BPM, Energy).
    * This script attempts to use **ReccoBeats** as a primary source for audio data.
    * It includes a fallback to the official API.
    * If both fail (Error 403), the script will automatically **skip** audio filters and continue generating the playlist without crashing.

---

## 🚀 Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/JanHermanS/spotify-mixer.git](https://github.com/JanHermanS/spotify-mixer.git)
   cd spotify-mixer
   ```

2. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```
   *(Requires `spotipy` and `requests`)*

---

## 🔑 Setup: Getting your API Keys

To use this tool, you need to create a simple "App" in the Spotify Developer Dashboard.

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Log in with your Spotify account.
3. Click **"Create App"**.
4. Fill in a name (e.g., "My Playlist Mixer") and description.
5. **Crucial Step (Redirect URI):** In the **Redirect URIs** field, enter:
   ```text
   http://localhost:8888/callback
   ```
   *(Don't forget to click "Add" and "Save" at the bottom)*.
6. **Crucial Step (API & User Whitelist):** Go to the **Settings** of your new app.
   * Under the **APIs used** tab (or Basic Information), explicitly add/check **Web API**.
   * Under the **User Management** tab, add the exact email address associated with your Spotify Premium account to whitelist yourself.
7. Copy the **Client ID** and **Client Secret**.

### Configuration

Create a new file (e.g., `my_radio.json`) based on `config.example.json`. Paste your keys at the top:

```json
{
  "credentials": {
    "client_id": "PASTE_YOUR_CLIENT_ID_HERE",
    "client_secret": "PASTE_YOUR_CLIENT_SECRET_HERE",
    "redirect_uri": "http://localhost:8888/callback"
  },
  "workflow": [ ... ]
}
```

### Basic Workflow Structure

The `workflow` is a list of actions executed from top to bottom. Each action produces an output (in memory) which can be used as input for the next action.

```json
"workflow": [
  {
    "comment": "1. Get tracks from a playlist",
    "action": "source",
    "id": "37i9dQZF1DXcBWIGoYBM5M",
    "hydrate": "auto",
    "output": "my_source_tracks"
  },
  {
    "comment": "2. Save them to your own playlist",
    "action": "save",
    "input": "my_source_tracks",
    "create_new": true,
    "name": "My Daily Mix",
    "shuffle": true
  }
]
```

---

## 🛠️ Usage

Run the script with Python, pointing it to your configuration file:

```bash
python spotify_mixer.py my_radio.json
```

**First Run:**
On the first run, a browser window will open (or a link will appear in the console). Log in to Spotify to authorize the app. This creates a hidden `.cache` file in the script directory so you don't have to log in again.

---

## 📚 Workflow Actions Reference

The logic is defined in the `workflow` array in your JSON file. The script executes these actions sequentially.

### Input & Sources

* **`source`**: Fetches tracks from a Spotify Playlist, Album, or Artist.
  * `id`: The Spotify ID (e.g., `37i9dQZF1DXcBWIGoYBM5M`).
    * Use `"me"` for your "Liked Songs".
    * **New:** Use `"top_tracks_short"`, `"top_tracks_medium"`, or `"top_tracks_long"` to fetch your personal top tracks (4 weeks, 6 months, or all time).
  * `hydrate`: Controls metadata fetching.
    * `"true"`: Fetches full metadata (Artist, Album, Images). **Slower**, but required if you plan to use `artist_separation` or `filter_artist`.
    * `"false"`: Only fetches track IDs. **Very fast**. Use this for large lists that you only want to mix or exclude.
    * `"auto"` (Default): Uses `"false"` for API calls (since API returns full data) but switches to `"true"` if the scraper is used (since scraping only retrieves IDs and lacks metadata).
* **`source_file`**: Loads tracks from a local file.
  * `filename`: Path to `.json` (database) or `.txt` file.
* **`sync_local_db`**: **(Powerful)** Syncs a Spotify playlist to a local JSON database.
  * **The "Inbox" Workflow (How it works in practice):**
    1. Create a playlist in Spotify (e.g., "Blacklist Inbox").
    2. Whenever you hear a song you hate, add it to that playlist.
    3. Configure this script with `clear_source: true`.
    4. When the script runs, it saves the song to `db_blacklist.json` and **empties** the Spotify playlist.
    5. **Result:** You have a permanent, growing local database of blocked songs, while your Spotify playlist stays clean and ready for new additions.
  * `mode`: `"append"` (add new items) or `"remove"` (remove items found in playlist from DB).
  * `store_type`: `"tracks"` (block specific songs) or `"artists"` (block the artist globally).
  * `clear_source`: `true` (Recommended) to wipe the Spotify playlist after syncing.

### Manipulation & Mixing

* **`mix`**: Combines multiple previous outputs into one pool.
  * `inputs`: Array of output names (e.g., `["src_hits", "src_classics"]`).
* **`sample`**: Randomly picks *N* tracks from the input.
  * `amount`: Number of tracks to keep.
* **`slice`**: Takes the *first N* tracks (useful after sorting).
  * `amount`: Number of tracks to keep.
* **`dedup`**: Removes duplicate tracks based on URI.
* **`inject`**: Inserts tracks from one list into another at regular intervals.
  * `input`: The base list.
  * `inject_input`: The list to inject.
  * `every`: Interval (e.g., 10).
  * `variance`: Randomness (e.g., 2 means interval is 8-12).

### Filtering

* **`filter_genre`**: Keeps or removes tracks based on artist genres.
  * `genres`: List of keywords (e.g., `["rock", "metal"]`).
  * `mode`: `"include"` or `"exclude"`.
* **`filter_artist`**: Removes tracks if the artist appears in a blacklist.
  * `blacklist_input`: The input containing blocked tracks or artists (usually loaded via `source_file` from a database).
* **`filter_exclude`**: Removes specific tracks (by ID) found in another list.
  * `exclude_input`: The input containing tracks to remove.
* **`filter_audio`**: Filters by BPM or Energy.
  * `min_bpm` / `max_bpm`: Tempo range.
  * `min_energy` / `max_energy`: Energy (0.0 - 1.0).
  * `fallback`: Set to `"none"` to return an empty list on API failure, or default to keeping all tracks.
* **`season`**: Checks the current date and only includes tracks if the month matches.
  * `cases`: Array of objects with `months` (e.g., `[12]`) and `sources`.

### Ordering & Saving

* **`sort`**: Sorts by `popularity` or other attributes.
* **`weighted_shuffle`**: A smart shuffle that prioritizes popular tracks but keeps the order random.
  * `factor`: Higher number = more randomness. Lower number = stricter popularity sort.
* **`artist_separation`**: Reorders the list to ensure the same artist doesn't play within *N* tracks.
  * `min_distance`: e.g., `10`.
* **`save`**: Pushes the result to a Spotify Playlist.
  * `id`: Target Playlist ID (Optional if `create_new` is true).
  * `create_new`: `true` to automatically create a brand new playlist on your account (useful to solve permission errors).
  * `name`: Name for the new playlist (used with `create_new`).
  * `description`: Description for the new playlist.
  * **Note:** This performs a "Wipe & Write" (clears the playlist first) to ensure exact syncing.

---

## 📂 Example Workflows

Check the `Examples/` folder for inspiration:
* `config.example.json`: Basic starter template.
* `workflow_maintenance.json`: How to manage local blacklists.
* `workflow_advanced_radio.json`: A full radio station logic with seasons and injections.

---

## 📄 License

This project is open-source. Feel free to modify and use it for your personal automations.