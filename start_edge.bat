@echo off
rem Debug port is reachable from this computer only. Add --use-fake-ui-for-media-stream ONLY if Momo must
rem speak into calls (CALL_AUDIO_DEVICE set); it lets every site in this window use the microphone silently.
start "" msedge --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="%USERPROFILE%\MomoEdge" --no-first-run --autoplay-policy=no-user-gesture-required