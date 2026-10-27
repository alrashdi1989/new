# X Media Downloader (Android)

Paste a public X profile URL → tap **Download** → photos and videos land in
`Download/X-media/<username>/` on your phone. Re-running only fetches new posts.

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

## 2. Get your `auth_token` (one time)

X only shows a profile's Media tab to logged-in users, so the app needs your
login cookie:

1. On a computer, log in to **x.com** in Chrome/Edge.
2. Press **F12** → **Application** tab → **Cookies** → `https://x.com`.
3. Copy the value of **auth_token**.
4. Paste it into the second box in the app. It's saved on the phone only.

Treat this value like a password — anyone who has it can use your X account.
If it stops working, log in again and copy the new one.

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
