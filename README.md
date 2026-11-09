# Media Downloader for X and TikTok (Android)

Paste a public X profile URL → tap **Download** → photos and videos land in
`Download/X-media/<username>/` on your phone, and they show up in your Gallery. Re-running only fetches new posts.

## 1. Build the APK (free, no PC setup)

1. Create a new **GitHub** repository and upload everything in this folder
   (including the hidden `.github` folder).
2. Open the repo's **Actions** tab — the "Build APK" job starts by itself.
   First build takes ~20–30 min (later builds are faster thanks to caching).
3. When it's green, open the run and download **x-media-downloader-apk** (a zip
   containing the `.apk`).
4. Copy the APK to your phone, open it, and allow "Install unknown apps".

> Have Linux / WSL? You can also build locally: `pip install buildozer cython`
> then `buildozer android debug` in this folder.

## 2. Log in (one time)

Tap **Log in to X** in the app, sign in on the X page that opens, and the app
saves your login automatically (button turns into "Logged in ✓"). Press Back
to cancel. Using a spare X account is safer if you download a lot.

(Alternative: paste an `auth_token` cookie copied from a desktop browser —
F12 → Application → Cookies → https://x.com → auth_token.)

## 3. Use it

- Paste a profile link, e.g. `https://x.com/nasa` (or just `@nasa`).
- Tick Photos / Videos / Reposts, tap **Download**.
- If Android blocks saving files, go to Settings → Apps → X Media Downloader →
  Permissions → Files and media → Allow.

## Quicker alternative: Termux (no APK build)

Install **Termux** from F-Droid, then:

```
pkg install python
termux-setup-storage
pip install gallery-dl
gallery-dl -o 'cookies={"auth_token":"PASTE_TOKEN"}' -d ~/storage/downloads/X-media https://x.com/USERNAME/media
```

## Save location and permission

On first launch the app asks for file access (Android: "Allow access to manage
all files"). Tap **Change** under *Save to* to pick any folder; each profile gets
its own subfolder there, and files show up in Gallery / Google Photos.

## TikTok and background downloads

Paste any TikTok profile (`tiktok.com/@user`), video, photo post or short share
link (`vm.tiktok.com/...`). TikTok now hides profile video lists from logged-out visitors, so log in under **TikTok account** to download profiles (single video links may work without it) (use username/email + password, not Google).
TikTok files go to `<Save to>/TikTok/<user>/`.

Downloads run in a background service with a notification, so you can leave
the app or lock the phone. Tap **Stop** in the app to cancel.

## Snapchat

Paste a public profile link (`snapchat.com/add/<user>` or `snapchat.com/@<user>`).
The app downloads the current story (last 24 hours) into `Snapchat/<user>/Story/`
all saved highlights into `Snapchat/<user>/Highlights/`, and the Spotlight
videos shown on the profile into `Snapchat/<user>/Spotlight/`. Run it again later
to pick up new stories; snaps you already have are skipped.

## Instagram

Log in once (Instagram account → Log in, with username/email + password).
Paste a profile link to download Stories, Highlights and Posts (incl. reels)
into `Instagram/<user>/Stories|Highlights|Posts/`, or paste a single post,
reel, story or highlight link. Instagram is slow on purpose (6–12 s between
requests) to avoid account restrictions; consider using a spare account.

## Several links at once

Paste several links into the box (one per line, or tap **+ Paste** after
copying each one). Tap **Download N links**: they download one after another
in the background, each into its own folder. One failing link doesn't stop the
rest, and **Stop** cancels the remaining ones.

## Facebook

Log in under **Facebook account** (recommended — Facebook stops showing photos
to logged-out visitors quickly). Paste a profile or Page link to download its
photos, or paste a photo, album, video, reel or shared (`facebook.com/share/…`,
`fb.watch/…`) link. Facebook doesn't offer a profile's full video list or
stories to this engine. Files go to `Facebook/<name>/`.

## Choose what to download

Under **From profiles**, pick any of **Stories, Highlights, Posts, Spotlight**.
They apply where a site has that content: Snapchat (story, highlights,
Spotlight), Instagram (stories, highlights, posts), TikTok (posts, stories).
X and Facebook profiles always download their media/photos. Your choice is
remembered.

## YouTube

Paste a video, Shorts, playlist or channel link. Videos download in HD (up to
1080p, H.264 + AAC in MP4). YouTube sends HD video and sound separately; the
app joins them on the phone (PyAV, no re-encoding). Turn **Videos** off under
*Include* to save audio only (M4A). Files go to `YouTube/<channel>/`.
YouTube's terms only allow downloading where YouTube offers it, your own
videos, or suitably licensed content.

## Any other website

Paste a link from any site. The app first looks for a video (yt-dlp supports
1000+ sites and videos embedded in ordinary pages), then for images (gallery-dl).
Files go to `Web/<site>/`. DRM-protected services (Netflix, Disney+, Spotify…)
can't be downloaded.

## Stories as one video

Turn on **Stories as one video** under *From profiles* (with **Stories** on).
After downloading a Snapchat, Instagram or TikTok story, the app joins its
photos (5 s each) and videos into `<name> story <date>.mp4` (720x1280, 30 fps,
H.264 + AAC) next to the Story folder. The separate parts are kept. A new video
is only made when the story has changed.

## Favorites

Paste profile links and tap **Save** to keep them under **Favorites**. Tap
**Get new** next to a profile (or **Get new from all**) to download only what's
new since last time — already-downloaded posts are skipped automatically.
Tap **×** to remove a favorite. Favorites are grouped by app; tap a group name or **+ / –** to expand or collapse it, and **Get new** on a group header to update that whole group.

## v5.2
- **Sound in videos:** Instagram now downloads the version of each video with sound built in. Facebook sends a video's picture and sound as two files; the app now downloads both and joins them into one video with sound.
- **Accounts at the same time:** choose 1, 2 or 3. With 2 or 3, the links (or "Get new from all") are shared between background workers that run side by side. Different sites are put side by side first. Each worker shows its own notification while it runs, and one summary appears when all are done.

## v5.3
- **Search favorites:** type part of a name, or the start of an app name ("tik", "insta"), to narrow the list. "Get new" then gets only the accounts shown.

## v6.0
- **Tabs:** Download, Favorites, Files and Settings, with a tab bar at the bottom. While a download runs, a progress strip shows on the other tabs (tap it to see the log).
- **Files tab:** every account you downloaded, grouped by app, with file count and size. Open an account to see its photos and videos (newest first), tap to open one, or tap Select to delete some or all (asks first; they're also removed from Gallery and won't be downloaded again).
- **Automatic checks (Settings):** Off, every 6 h, every 12 h or daily. A separate background worker gets new posts from all favorites and notifies you only when there's something new. It keeps going without opening the app; after a phone restart it resumes the next time the app is opened.
- Photos / Videos / X reposts choices are remembered.

## v6.1
- **File names:** new downloads are named after the profile and the time the post was published, e.g. `natgeo 2026-11-06 16-24-05.jpg` (several files in one post get (2), (3)…). Settings → File names switches back to the site's own names. YouTube and websites keep the video title.

## v6.2
- **Threads:** added Threads (threads.com / threads.net) as a supported site. Paste a profile link to get its public posts, or a single post link. Photos and videos (including carousels) download with the same profile+time names. A Threads login under Settings is only needed for private profiles you follow. Built from scratch, since no downloader library supports Threads yet.

## v6.3
- **Floating button:** turn it on in Settings to show a small app button on top of other apps. Copy a link anywhere, tap the button, and it downloads straight away without opening the app. Drag it to move it; turn it off to hide it. Needs the one-time "Display over other apps" permission.
- Internally, link handling moved to a shared module so the floating button builds the same jobs with your saved settings (folder, logins, options).

## v6.4
- **Floating button fix:** the bubble now shows reliably. It was being drawn through the app window, which a background service cannot use; it now runs on its own thread inside the service. Added a check that warns if the service does not start, and a float.log for diagnosing.

## v6.5
- **Floating button:** added a paste box. Tap the bubble; if your phone blocks reading the clipboard in the background (Android 10+), a small box opens where you paste the link (system Paste always works) and tap Download. On phones that allow the background read, it still downloads in one tap with no box. More logging in float.log.

## v6.6
- **Floating button, robust version:** removed the in-overlay paste box (it was crashing the background service when building its UI). Now, if the phone blocks reading the clipboard in the background, tapping the bubble briefly opens the app, which reads the copied link, starts the download, and drops you back to where you were. On phones that allow the background read it still downloads silently. Added a crash logger to float.log.
