# Not Quite My Tempo sound effects

Procedurally synthesized by `../gen_sfx.py`. No recorded or downloaded audio is used.
Every random generator is seeded from the sound name, so output is bit-identical on every run.

Format is mono 16-bit PCM WAV at 48000 Hz. Each file starts on its transient at t=0
(4 ms fade-in), ends with a fade-out, has no DC offset and peaks at -3 dBFS unless
marked quieter by design below. `sfx_manifest.json` lists duration, peak, category and
description for every file.

## Regenerate

```
cd ~/not-quite-my-tempo-assets
python3 gen_sfx.py
```

The script prints an analysis table (peak, RMS, crest, spectral centroid, onset) and
raises if any sound is silent, clipped, mistimed, off its duration range or loses its
expected character (cowbell partials, cymbal decay, low footsteps, bright claps).

## Sounds

| name | seconds | peak dBFS | description |
|---|---|---|---|
| `foot_step_1` | 0.132 | -3.0 | Dress shoe heel and toe on hardwood, variant 1 (mid board resonance). |
| `foot_step_2` | 0.143 | -3.0 | Dress shoe step on hardwood, variant 2 (lower boards, longer roll, clickier heel). |
| `foot_step_3` | 0.127 | -3.0 | Dress shoe step on hardwood, variant 3 (tighter boards, quick heavy toe). |
| `foot_step_4` | 0.158 | -3.0 | Dress shoe step on hardwood, variant 4 (slow heel to toe roll, light toe). |
| `foot_stomp` | 0.301 | -3.0 | Heavy aggressive stomp on hardwood with board rattle and room. |
| `foot_scuff` | 0.360 | -3.0 | Shoe twist and drag on hardwood with a short leather squeak. |
| `clap_1` | 0.056 | -3.0 | Single sharp hand clap, mid cavity with a tiny flam. |
| `clap_2` | 0.053 | -3.0 | Single sharp hand clap, brighter and tighter, no flam. |
| `clap_3` | 0.064 | -3.0 | Single sharp hand clap, lower and fuller, wider flam. |
| `clap_big` | 0.089 | -3.0 | One huge cupped stopping clap with a big room bloom. |
| `slap_1` | 0.103 | -3.0 | Open-hand face slap, meaty skin smack, variant 1. |
| `slap_2` | 0.105 | -3.0 | Open-hand face slap, heavier and fleshier, variant 2. |
| `slap_3` | 0.105 | -3.0 | Open-hand face slap, sharper crack, variant 3. |
| `fist_slam_stand` | 0.750 | -3.0 | Fist hammering a steel music stand desk with sheet music, desk ring and clamp chatter. |
| `whoosh_small` | 0.260 | -3.0 | Quick arm flick whoosh. |
| `whoosh_big` | 0.520 | -3.0 | Violent full-arm swing whoosh with low air rumble. |
| `whoosh_spin` | 0.620 | -3.0 | Spinning flat object flying past, pulsing whirr with a Doppler drop, about 0.6 s. |
| `cloth_rustle_1` | 0.450 | -12.0 | Shirt movement rustle, quiet by design (peak -12 dBFS). |
| `cloth_rustle_2` | 0.620 | -12.0 | Trousers movement rustle, heavier and lower, quiet by design (peak -12 dBFS). |
| `body_thud` | 0.328 | -3.0 | Heavy weight shift or lunge landing on the wooden floor. |
| `stand_rattle` | 0.987 | -3.0 | Metal music stand jostled, a burst of small clanks. |
| `stand_crash` | 1.438 | -3.0 | Metal music stand toppling and clattering on the wood floor. |
| `paper_grab` | 0.284 | -3.0 | Snatching sheet music, crackle and swish. |
| `paper_flutter` | 0.800 | -3.0 | Pages flying through the air, irregular flaps, about 0.8 s. |
| `paper_land` | 0.433 | -3.0 | Pages slapping onto the floor one after another. |
| `page_flip` | 0.438 | -6.0 | Turning one page on a stand, quieter by design (peak -6 dBFS). |
| `chair_grab` | 0.800 | -3.0 | Grabbing a folded metal chair, tube clank and hinge chatter. |
| `chair_impact` | 1.600 | -3.0 | Folded metal chair slamming into something and clattering on the floor. |
| `drum_grab` | 0.304 | -3.0 | Picking up a snare drum, shell knock and a slight snare buzz. |
| `drum_impact` | 1.185 | -3.0 | Snare drum crashing to the floor and bouncing, snares rattling. |
| `cymbal_grab` | 0.391 | -3.0 | Gripping a crash cymbal edge, finger tick and a faint choked shimmer. |
| `cymbal_impact` | 2.800 | -3.0 | Crash cymbal hitting and ringing, bright shimmering tail of about 2.8 s. |
| `cowbell_hit_1` | 1.000 | -3.0 | Hard stick hit on a cowbell, partials near 540 and 800 Hz, open ring. |
| `cowbell_hit_2` | 0.874 | -3.0 | Hard stick hit on a cowbell nearer the mouth, brighter and shorter. |
| `watch_tap` | 0.082 | -9.0 | Fingernail tapping a watch face, quieter by design (peak -9 dBFS). |
| `finger_snap` | 0.047 | -3.0 | Finger snap, bright crack with a small palm thump. |
| `chair_creak` | 0.500 | -6.0 | Player shifting on a padded metal chair, vinyl and joint creak, quieter by design (peak -6 dBFS). |
| `instrument_handle` | 0.228 | -3.0 | Soft clunk of picking up or setting down an instrument. |
| `sax_keys` | 0.315 | -6.0 | Saxophone key pads clacking, quieter by design (peak -6 dBFS). |
| `trumpet_valves` | 0.324 | -6.0 | Three trumpet valves pressed and released, quieter by design (peak -6 dBFS). |
| `trombone_slide` | 0.330 | -3.0 | Trombone slide swish ending on the stop, about 0.3 s. |
| `spit_valve` | 0.960 | -3.0 | Trombone water key click, a pfft and a few drips. |
| `guitar_handle` | 0.770 | -3.0 | Guitar body thump with open strings brushed. |
| `bass_handle` | 0.582 | -3.0 | Upright bass body thump with a low string buzz. |
| `piano_bench_creak` | 0.559 | -6.0 | Wooden piano bench creak with a small leg knock, quieter by design (peak -6 dBFS). |
| `door_slam` | 0.554 | -3.0 | Heavy wooden door slammed shut in a wood-panelled room, latch click and frame rattle. |
