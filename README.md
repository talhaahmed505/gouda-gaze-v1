# gouda-gaze

A self-hosted kitty cam web app running on a Raspberry Pi. Built for Gouda.

Streams live video from an Amcrest PTZ IP camera with a custom Flask frontend for camera control and remote access over Tailscale. The camera runs on a sandboxed local network with no direct internet access — all traffic goes through the Pi.

The backend is Python/Flask talking to the camera over its CGI and RPC2 APIs. Video is handled by go2rtc which takes the camera's RTSP stream and serves it as WebRTC. Everything runs in Docker.

## Features

Live WebRTC stream with PTZ controls, digital zoom, and a hardware privacy mode that physically flips the camera into its base. You can take snapshots from the live view and browse them in a gallery. Stream settings like resolution, framerate, and bitrate are configurable from the UI without touching the camera directly.

## Stack

Python, Flask, go2rtc, Docker Compose, Tailscale.

## Setup

Copy `.env.example` to `.env` and fill in your camera credentials, IP, and Tailscale hostname, then run `docker compose up -d`. See `.env.example` for the full list of variables.

## Planned

User auth and access control, PTZ preset management, motion detection with auto-snapshot, camera status indicator, day/night mode, image quality controls, timelapse.