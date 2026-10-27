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
link (`vm.tiktok.com/...`). No TikTok login is needed for public accounts; tap **Log in** under TikTok account for private accounts (use username/email + password, not Google).
TikTok files go to `<Save to>/TikTok/<user>/`.

Downloads run in a background service with a notification, so you can leave
the app or lock the phone. Tap **Stop** in the app to cancel.

## Snapchat

Paste a public profile link (`snapchat.com/add/<user>` or `snapchat.com/@<user>`).
The app downloads the current story (last 24 hours) into `Snapchat/<user>/Story/`
and all saved highlights into `Snapchat/<user>/Highlights/`. Run it again later
to pick up new stories; snaps you already have are skipped.
