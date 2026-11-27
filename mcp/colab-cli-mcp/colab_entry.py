"""Run the official CLI with container-local process limits and safe diagnostics."""
import resource

from colab_cli.cli import main


if __name__ == "__main__":
    # Applies to downloads, notebook export and the CLI's persistent logs.
    cap = 32 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    # Rich crash reports can include locals such as ADC/session tokens.
    from colab_cli.cli import app

    app.pretty_exceptions_show_locals = False
    main()
