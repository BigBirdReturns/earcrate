import sys

if len(sys.argv) > 1 and sys.argv[1] == "midi":
    from earcrate.midi.cli import midi_main

    sys.exit(midi_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "live":
    from earcrate.live.cli import live_cli_main

    sys.exit(live_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "live-audio":
    from earcrate.live.audio_cli import live_audio_cli_main

    sys.exit(live_audio_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "reference":
    from earcrate.study.reference_cli import reference_cli_main

    sys.exit(reference_cli_main(sys.argv[2:]))

from earcrate.cli import main

sys.exit(main())
