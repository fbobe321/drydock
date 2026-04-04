import sys
from .cli import LogAnalyzerCLI

def main():
    cli = LogAnalyzerCLI()
    sys.exit(cli.run())

if __name__ == "__main__":
    main()
