# Demo video

The submission video is generated, not screen-recorded by hand, so it can be
rebuilt from current data whenever the numbers move.

    node docs/demo/render.mjs          # one 1920x1080 frame per scene
    ffmpeg -f concat -safe 0 -i list.txt \
      -vf "fps=30,format=yuv420p" -c:v libx264 -crf 20 out.mp4

`film.html` holds the scenes and their timings; every figure in it comes from
the live account or the agent's own ledger. Output: 1920x1080, h.264, 3:39.
