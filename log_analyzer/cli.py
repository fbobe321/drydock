import argparse
from log_analyzer.analyzer import LogAnalyzer

class LogAnalyzerCLI:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Simple Log Analyzer CLI")
        self.parser.add_argument("file", help="Path to the log file")
        self.parser.add_argument("--level", help="Filter by log level (INFO, ERROR, etc.)")
        self.parser.add_argument("--search", help="Search for a pattern in messages")

    def run(self):
        args = self.parser.parse_args()
        analyzer = LogAnalyzer(args.file)

        try:
            entries = analyzer.parse()
        except Exception as e:
            print(f"Error: {e}")
            return 1

        if args.level:
            entries = analyzer.filter_by_level(args.level)

        if args.search:
            entries = analyzer.search(args.search)

        print(f"--- Log Analysis for {args.file} ---")
        print(f"Total entries found: {len(entries)}")
        
        summary = analyzer.get_summary()
        if summary:
            print("\nSummary by Level:")
            for level, count in summary.items():
                print(f"  {level}: {count}")

        print("\nEntries:")
        for entry in entries:
            print(f"[{entry.timestamp}] {entry.level}: {entry.message}")
        
        return 0
