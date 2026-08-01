import sys

if len(sys.argv) > 1 and sys.argv[1] == "reader":
    from earcrate.reader.cli import reader_main

    sys.exit(reader_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "project":
    from earcrate.project.gate8_cli import main as project_main

    sys.exit(project_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "midi":
    from earcrate.midi.cli import midi_main

    sys.exit(midi_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "mix":
    from earcrate.mix.cli import mixscore_cli_main

    sys.exit(mixscore_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "buffalo":
    from earcrate.specimen.cli import specimen_cli_main

    sys.exit(specimen_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "floor":
    from earcrate.floor.cli import floor_cli_main

    sys.exit(floor_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "estate":
    from earcrate.estate.cli import estate_cli_main

    sys.exit(estate_cli_main(sys.argv[2:]))

if len(sys.argv) > 1 and sys.argv[1] == "homelab":
    from earcrate.estate.homelab_cli import homelab_cli_main

    sys.exit(homelab_cli_main(sys.argv[2:]))

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
