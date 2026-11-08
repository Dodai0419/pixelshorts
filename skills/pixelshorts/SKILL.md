---
name: pixelshorts
description: Use when the user wants an animated short, a YouTube Short, Reel or TikTok about a story, history or science fact, without an API key, GPU or video generator. Renders a story file to a vertical video with isometric pixel dioramas, score, SFX and captions.
---

# pixelshorts

Renders a story text file into a vertical mp4. Needs Python and ffmpeg, and no network.

## Steps

1. Pick one surprising true story with a physical cause and effect. Write the facts and their source as `#` comments at the top of the file. Do not invent facts.
2. Write the script with the formula below, about 210 to 260 words.
3. Run `check` and fix every refusal and note.
4. Run `stills` and look at the PNGs. Fix any beat where the picture does not fit the line.
5. Render, then run `qa`. Fix what it reports.

```bash
python pixelshorts.py check story.txt
python pixelshorts.py stills story.txt -o stills
python pixelshorts.py render story.txt -o short.mp4
python pixelshorts.py qa short.mp4
```

Exit codes: `0` success, `1` `qa` found a problem, `2` refused or bad input.

## The format

```text
# Tacoma Narrows, 1940. Source: Washington State DOT history page.
title: The Bridge That Danced Itself to Death
bridge storm collapse: A brand new American bridge tore itself apart in a breeze.
town day crowd: It was 1940 in Washington State, and the town was proud.
```

Each line is `place light action: narration`.

| Word | Choices |
|---|---|
| place | `bridge`, `town`, `harbor`, `lab`, `field` |
| light | `day`, `golden`, `night`, `storm` |
| action | `stand`, `walk`, `march`, `crowd`, `talk`, `shake`, `collapse`, `flash`, `rain`, `boom` |

A beat lasts as long as its words at 3.7 words per second. `collapse` removes the place's main structure for later beats, except in the first beat, where it is a flash-forward hook.

## The script formula

1. **Hook:** the absurd fact in one line.
2. **Setting:** year, place, pride.
3. **Action:** three or four beats, each a little bigger.
4. **Snap:** the moment it breaks. Use `collapse`, `boom` or `flash`.
5. **Deadpan pair:** "Luckily ..." then "Unluckily ...".
6. **Explanation:** one idea through an everyday comparison.
7. **Consequence:** the rule it created.
8. **Callback:** an ending that returns to the hook.

Never narrate what the viewer already sees.

## Options

- `--voice voice.wav`: the picture stretches to fit the narration and the score ducks under it.
- `--no-captions`: required for narration that is not ASCII, because the pixel font is ASCII only. Use it with `--voice`.
- `--seed N`: another cast and camera drift. The same seed gives the same frames.
- `--2k`: 1440x2560 instead of 1080x1920.

## Limits

No speech is generated. Without `--voice`, the captions tell the story. Beats scale evenly to a voice file, so a line can drift from its word when the speaker changes pace. There are five places, so very specific settings need a nearby stand-in.
