# gouda-gaze
Locally hosted kitty came for the loml, Gouda


This will also serve to teach me how to make an actual app


All of the Python, HTML, CSS, and the Dockerfile was vibe coded with Gemini. Currently the app acts as a camera passthrough for my USB web cam.
In the future (one i get it), there will a proper PTZ IP camera connected to the private Pi network and the project will have to be rewritten to support it. It will be a simplification though because a lot of the video processing can be done on the camera itself as it will jut output a RSTP stream rather than the Pi having to run ffmpeg.