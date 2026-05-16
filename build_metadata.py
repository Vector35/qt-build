#!/usr/bin/env python3

import datetime
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path


_SECRET_KEY_PARTS = ("TOKEN", "PASSWORD", "PASS", "SECRET", "PIN", "KEY", "CREDENTIAL", "CERT")
_CI_ENV_VARS = ("JOB_NAME", "BUILD_NUMBER", "BUILD_URL", "BRANCH_NAME", "CHANGE_ID")


def _redact(key, value):
	if value is None:
		return None
	if any(part in key.upper() for part in _SECRET_KEY_PARTS):
		return "<redacted>"
	return value


def _run(cmd, cwd=None):
	try:
		proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
	except FileNotFoundError:
		return {"value": None, "error": f"{cmd[0]} not found"}
	except Exception as e:
		return {"value": None, "error": str(e)}
	value = proc.stdout.strip() or proc.stderr.strip()
	if proc.returncode != 0:
		return {"value": value or None, "error": f"exit {proc.returncode}"}
	return {"value": value, "error": None}


def _first_line(result):
	value = result.get("value")
	if not value:
		return None
	return value.splitlines()[0]


def _git_metadata(repo_root):
	if not shutil.which("git"):
		return {"remote_url": None, "branch": None, "commit": None, "dirty": None, "error": "git not found"}
	remote = _run(["git", "config", "--get", "remote.origin.url"], cwd=repo_root)
	branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
	commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
	status = _run(["git", "status", "--porcelain"], cwd=repo_root)
	errors = [r["error"] for r in (remote, branch, commit, status) if r.get("error")]
	return {
		"remote_url": remote.get("value"),
		"branch": branch.get("value"),
		"commit": commit.get("value"),
		"dirty": bool(status.get("value")) if status.get("error") is None else None,
		"error": "; ".join(errors) if errors else None,
	}


def _tool_versions():
	tools = {
		"cmake": ["cmake", "--version"],
		"ninja": ["ninja", "--version"],
		"git": ["git", "--version"],
		"clang": ["clang", "--version"],
		"gcc": ["gcc", "--version"],
	}
	if sys.platform == "win32":
		tools["cl"] = ["cl"]
	if sys.platform == "darwin":
		tools["xcodebuild"] = ["xcodebuild", "-version"]
		tools["xcode-select"] = ["xcode-select", "-p"]
		tools["sw_vers"] = ["sw_vers"]
	tools["poetry"] = ["poetry", "--version"]
	tools["mise"] = ["mise", "--version"]
	return {name: _run(cmd) for name, cmd in tools.items()}


def emit_build_metadata(repo_name, artifact_path, paths=None, versions=None, options=None, env_var_names=()):
	paths = {k: (str(v) if v is not None else None) for k, v in (paths or {}).items()}
	versions = dict(versions or {})
	options = dict(options or {})
	repo_root = paths.get("repo_root") or str(Path(__file__).resolve().parent)
	env_names = list(dict.fromkeys(list(_CI_ENV_VARS) + list(env_var_names or ())))
	environment = {name: _redact(name, os.environ.get(name)) for name in env_names}
	metadata = {
		"schema_version": 1,
		"generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
		"repo_name": repo_name,
		"repository": {"root": repo_root, **_git_metadata(repo_root)},
		"ci": {name: environment[name] for name in _CI_ENV_VARS},
		"host": {
			"platform": platform.platform(),
			"system": platform.system(),
			"release": platform.release(),
			"version": platform.version(),
			"architecture": platform.architecture()[0],
			"machine": platform.machine(),
			"processor": platform.processor(),
			"hostname": socket.gethostname(),
		},
		"python": {"executable": sys.executable, "version": sys.version},
		"tools": _tool_versions(),
		"paths": paths,
		"versions": versions,
		"options": options,
		"environment": environment,
	}
	if sys.platform == "win32":
		metadata["windows_build_environment"] = {
			name: _redact(name, os.environ.get(name))
			for name in ("VisualStudioVersion", "VSINSTALLDIR", "VCINSTALLDIR", "VCToolsVersion", "WindowsSDKVersion")
			if name in os.environ
		}
	print("\n=== Reproducibility preamble ===")
	print(f"Repository: {repo_name}")
	print(f"Repository root: {repo_root}")
	git = metadata["repository"]
	print(f"Git remote: {git.get('remote_url')}")
	print(f"Git branch: {git.get('branch')}")
	print(f"Git commit: {git.get('commit')}")
	print(f"Git dirty: {git.get('dirty')}")
	print(f"Host: {metadata['host']['platform']} {metadata['host']['machine']} ({metadata['host']['hostname']})")
	print(f"Python: {sys.executable} :: {platform.python_version()}")
	for name in sorted(metadata["tools"]):
		print(f"{name}: {_first_line(metadata['tools'][name])}")
	for section_name, section in (("Paths", paths), ("Versions", versions), ("Options", options), ("Environment", environment)):
		print(f"{section_name}:")
		for key in sorted(section):
			print(f"  {key}: {section[key]}")
	metadata_path = Path(artifact_path) / "build-metadata.json"
	Path(artifact_path).mkdir(parents=True, exist_ok=True)
	with metadata_path.open("w", encoding="utf-8") as f:
		json.dump(metadata, f, indent=2, sort_keys=True)
		f.write("\n")
	print(f"Build metadata written to: {metadata_path}")
	print("=== End reproducibility preamble ===\n")
	return metadata
