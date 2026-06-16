import argparse
import sys
import yaml
from pathlib import Path
from pydantic import ValidationError
from vad.contracts.models import EIP

def init_command(args):
    print("Initializing new EIP...")

def validate_command(args):
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(file_path, "r") as f:
            if file_path.suffix in [".yml", ".yaml"]:
                data = yaml.safe_load(f)
            else:
                import json
                data = json.load(f)
        EIP(**data)
        print("EIP is valid.")
    except ValidationError as e:
        print("EIP validation failed:", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading file: {e}", file=sys.stderr)
        sys.exit(1)

def diff_command(args):
    print("Diffing EIPs...")

def retro_command(args):
    from vad.evidence.bundle import EvidenceBundle
    from vad.feedback.retro import RetroAnalyzer
    from vad.memory.gateway import MemoryGateway
    from vad.memory.stores.local import LocalMemoryStore
    from vad.memory.redaction import Redactor
    
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(file_path, "r") as f:
            if file_path.suffix in [".yml", ".yaml"]:
                data = yaml.safe_load(f)
            else:
                import json
                data = json.load(f)
        
        bundle = EvidenceBundle(data)
        gateway = MemoryGateway(store=LocalMemoryStore(), redactor=Redactor())
        analyzer = RetroAnalyzer(gateway)
        
        result = analyzer.analyze(bundle)
        print("Retro analysis complete.")
        print(f"Learnings: {result['learning']}")
    except Exception as e:
        print(f"Error processing retro analysis: {e}", file=sys.stderr)
        sys.exit(1)

def mcp_run_command(args):
    from vad.adapters.mcp import serve
    serve()

def mcp_install_command(args):
    import os
    import json
    import sys
    from pathlib import Path

    client = args.client
    
    cursor_snippet = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "vad.adapters.mcp"],
        "enabled": True
    }
    
    claude_snippet = {
        "command": sys.executable,
        "args": ["-m", "vad.adapters.mcp"]
    }

    if client in [None, "claude"]:
        config_dir = Path.home() / ".claudecode"
        config_file = config_dir / "config.json"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            config_data = {}
            if config_file.exists():
                with open(config_file, "r") as f:
                    config_data = json.load(f)
            if "mcpServers" not in config_data:
                config_data["mcpServers"] = {}
            config_data["mcpServers"]["vad"] = claude_snippet
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            print(f"[SUCCESS] Configured Claude Code CLI extension at {config_file}")
        except Exception as e:
            print(f"[WARNING] Could not automatically configure Claude Code: {e}")
            
    if client in [None, "cursor"]:
        if sys.platform == "win32":
            appdata = Path(os.environ.get("APPDATA", "~/AppData/Roaming")).expanduser()
            cursor_dir = appdata / "Cursor"
        elif sys.platform == "darwin":
            cursor_dir = Path.home() / "Library" / "Application Support" / "Cursor"
        else:
            cursor_dir = Path.home() / ".config" / "Cursor"
            
        config_file = cursor_dir / "User" / "globalStorage" / "meta.mcp" / "config.json"
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_data = {}
            if config_file.exists():
                with open(config_file, "r") as f:
                    config_data = json.load(f)
            if "mcpServers" not in config_data:
                config_data["mcpServers"] = {}
            config_data["mcpServers"]["vad"] = cursor_snippet
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            print(f"[SUCCESS] Configured Cursor extension at {config_file}")
        except Exception as e:
            print(f"[WARNING] Could not automatically configure Cursor: {e}")

    print("\n" + "="*50)
    print("VAD MCP SERVER MANUAL CONFIGURATION GUIDE")
    print("="*50)
    print("If automatic configuration failed, or you are using another LLM CLI,")
    print("use the following details to register the VAD MCP Server:\n")
    print(f"Executable (Python): {sys.executable}")
    print(f"Arguments: -m vad.adapters.mcp\n")
    print("--- Claude Code (~/.claudecode/config.json) snippet ---")
    print(json.dumps({"mcpServers": {"vad": claude_snippet}}, indent=2))
    print("\n--- Cursor (~/.../User/globalStorage/meta.mcp/config.json) snippet ---")
    print(json.dumps({"mcpServers": {"vad": cursor_snippet}}, indent=2))
    print("="*50 + "\n")

def main():
    parser = argparse.ArgumentParser(prog="vad")
    subparsers = parser.add_subparsers(dest="command")
    
    # EIP commands
    eip_parser = subparsers.add_parser("eip")
    eip_subparsers = eip_parser.add_subparsers(dest="eip_command")
    
    init_parser = eip_subparsers.add_parser("init")
    
    validate_parser = eip_subparsers.add_parser("validate")
    validate_parser.add_argument("file", help="Path to EIP file")
    
    diff_parser = eip_subparsers.add_parser("diff")
    
    retro_parser = eip_subparsers.add_parser("retro")
    retro_parser.add_argument("file", help="Path to evidence bundle file")
    
    # MCP commands
    mcp_parser = subparsers.add_parser("mcp")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    
    mcp_subparsers.add_parser("run", help="Run the MCP stdio server")
    
    install_parser = mcp_subparsers.add_parser("install", help="Install/configure VAD CLI extension")
    install_parser.add_argument("client", nargs="?", choices=["claude", "cursor"], help="Target client for automatic installation")
    
    args = parser.parse_args()
    
    if args.command == "eip":
        if args.eip_command == "init":
            init_command(args)
        elif args.eip_command == "validate":
            validate_command(args)
        elif args.eip_command == "diff":
            diff_command(args)
        elif args.eip_command == "retro":
            retro_command(args)
        else:
            eip_parser.print_help()
    elif args.command == "mcp":
        if args.mcp_command == "run":
            mcp_run_command(args)
        elif args.mcp_command == "install":
            mcp_install_command(args)
        else:
            mcp_parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

