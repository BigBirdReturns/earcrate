import sys

if len(sys.argv) > 1 and sys.argv[1] == "midi":
    from earcrate.midi.cli import midi_main

    sys.exit(midi_main(sys.argv[2:]))

from earcrate.cli import main

sys.exit(main())
