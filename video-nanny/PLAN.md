# video-nanny — design + plan

iPhone-to-iPhone one-way video + audio streaming over local Wi-Fi, no internet,
no cloud. Use case: baby monitor / room monitor — one phone parked as the
camera, another phone as the viewer.

This file is the handoff from the initial brainstorm. Read it before resuming
implementation.

## Scope (MVP)

- One-way: camera → viewer. Not symmetric.
- Single viewer per camera.
- No auth (LAN-trusted).
- No recording, no multi-viewer, no two-way audio.
- Auto-reconnect on drop. Minimal error UI.
- Target: iOS 26+ (development phone is on 26.3.1).

## Locked design decisions

| Area | Choice | Why |
| --- | --- | --- |
| Networking | `Network.framework` + Bonjour (`_videonanny._tcp`) | MultipeerConnectivity throughput is too low for live H.264; we need real TCP. |
| Transport | TCP, single connection | Reliability > shaving milliseconds for nanny use case. |
| Discovery | Bonjour service, viewer browses, no manual IP | Zero-conf on the LAN. |
| Video pipeline | `AVCaptureSession` → `VTCompressionSession` (H.264) → TCP → `VTDecompressionSession` → `AVSampleBufferDisplayLayer` | Sub-second glass-to-glass; HW accelerated both ends. |
| Pixel format | `kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange` (420v) | Zero-copy into VideoToolbox HW encoder. |
| Video specs | 720p @ 30fps, ~2 Mbps, keyframe every 2s | Plenty for a nanny; cheap on battery and Wi-Fi. |
| Audio | `AVCaptureAudioDataOutput` → `AVAudioConverter` (AAC-LC) → TCP → decode → `AVAudioEngine` | Single-stream multiplex with video. |
| Audio specs | AAC-LC, 44.1 kHz mono, ~64 kbps | |
| App shape | Single SwiftUI app, role chosen at launch and persisted | Crib phone always boots into camera mode. |
| Project format | `.xcodeproj` generated from `project.yml` via [XcodeGen](https://github.com/yonaskolb/XcodeGen) | `project.pbxproj` is hostile to hand-author from a non-Mac. |

## Wire protocol

Single TCP stream. Every message has a 13-byte header followed by a payload:

```
[1B  type]
[4B  BE length of payload]
[8B  BE pts in microseconds]
[..  payload]
```

| type | meaning | payload |
| --- | --- | --- |
| 0 | video config | SPS + PPS, AVCC framing. Sent on connect, then with each keyframe. |
| 1 | video sample | One H.264 access unit, AVCC framing (4-byte length-prefixed NAL units). |
| 2 | audio config | AAC magic cookie / ASBD bytes the decoder needs. Sent once on connect. |
| 3 | audio sample | One AAC frame. |

Why a `pts` field even on TCP: lets the viewer drop or resync if it falls
behind, and gives audio/video a common timeline for lipsync.

## Project layout

```
video-nanny/
├── README.md
├── PLAN.md                          # this file
├── project.yml                      # XcodeGen spec → produces VideoNanny.xcodeproj
└── VideoNanny/
    ├── VideoNannyApp.swift          # @main, role-based root view
    ├── AppState.swift               # role persistence (UserDefaults)
    ├── ContentView.swift            # role picker
    ├── Camera/
    │   ├── CameraView.swift         # broadcasting UI
    │   ├── CameraSession.swift      # AVCaptureSession owner
    │   ├── VideoEncoder.swift       # VTCompressionSession (H.264)
    │   └── AudioEncoder.swift       # AVAudioConverter → AAC
    ├── Viewer/
    │   ├── ViewerView.swift         # browse + playback UI
    │   ├── ViewerSession.swift      # NWBrowser + NWConnection
    │   ├── VideoDecoder.swift       # VTDecompressionSession + AVSampleBufferDisplayLayer
    │   └── AudioDecoder.swift       # AAC → PCM → AVAudioEngine
    ├── Networking/
    │   ├── NetworkConstants.swift   # service type, framing constants
    │   ├── Frame.swift              # encode/decode the 13B header
    │   ├── CameraServer.swift       # NWListener, accepts one viewer
    │   └── ViewerClient.swift       # NWBrowser + NWConnection wrapper
    └── Resources/
        └── Assets.xcassets/
```

## Required Info.plist keys (declared via project.yml)

- `NSCameraUsageDescription`
- `NSMicrophoneUsageDescription`
- `NSLocalNetworkUsageDescription`
- `NSBonjourServices` = `["_videonanny._tcp"]`

## Build / run on the Mac

```bash
brew install xcodegen
cd video-nanny
xcodegen generate          # produces VideoNanny.xcodeproj
open VideoNanny.xcodeproj
# In Xcode: select your team in Signing & Capabilities, pick a real device, run.
# Run on both iPhones; pick "Camera" on one, "Viewer" on the other.
```

Notes:
- Simulator can't do real Wi-Fi P2P or use the camera — must run on physical
  devices.
- Both phones must be on the same Wi-Fi SSID and the network must allow
  mDNS/peer traffic (most home networks do; corporate/guest networks often
  block it).
- First launch will prompt for camera, microphone, and local network
  permissions. Approve all three.

## Implementation order

Each step ends in a runnable build that can flash to both phones.

1. **Scaffold** — `project.yml`, SwiftUI app skeleton, role picker that persists
   choice. Camera/Viewer views are placeholders.
2. **Networking handshake** — Bonjour publish on the camera, browse on the
   viewer, exchange one keepalive frame. Verify discovery + framing without any
   media yet.
3. **Video** — capture → encode → send → decode → display. Confirm live picture
   end-to-end before touching audio.
4. **Audio** — capture → AAC encode → mux into the same stream → decode →
   playback.
5. **Polish** — auto-reconnect on drop, dim the camera screen after 10s,
   surface basic errors (no permission, peer dropped, network changed).

## Out of scope for v1 (revisit later)

- PIN pairing / proper auth.
- Multiple viewers on one camera.
- Two-way push-to-talk.
- Recording to Photos / Files.
- Background mode on the viewer (would require PiP + extra entitlements).
- HEVC (H.265) — only worthwhile if 720p ~2 Mbps proves insufficient.
