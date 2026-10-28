[app]
title = Media Downloader
package.name = xmediadownloader
package.domain = org.personal
source.dir = .
source.include_exts = py,png,jpg,kv,json
version = 3.0
requirements = python3,kivy,android,pyjnius,sqlite3,openssl,requests,urllib3,certifi,charset-normalizer,idna,gallery-dl
orientation = portrait
services = Downloader:service.py:foreground
fullscreen = 0
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #111827

android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE,FOREGROUND_SERVICE,POST_NOTIFICATIONS,WAKE_LOCK
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.ndk = 25b
android.accept_sdk_license = True
p4a.branch = v2024.01.21
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 0
