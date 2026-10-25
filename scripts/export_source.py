"""Export an explicit source allowlist; never package local user data or caches."""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from launcher import __version__

FILES = ("README.md","README.en.md","requirements.txt","requirements-dev.txt",
         "main.py","build.py","publisher.json",
         ".gitignore",".gitattributes",".editorconfig")
DIRECTORIES = ("launcher","tests","assets","licenses","docs","scripts",".github")
EXCLUDED = {"__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",".git","data","dist","test-results"}


def source_files():
    files = [ROOT/name for name in FILES]
    files.extend(ROOT/name for name in ("LICENSE","LICENSE.md","LICENSE.txt","NOTICE","SECURITY.md","AGENTS.md") if (ROOT/name).is_file())
    for name in DIRECTORIES:
        directory = ROOT/name
        if not directory.is_dir(): raise FileNotFoundError(str(directory))
        for path in directory.rglob("*"):
            if not path.is_file() or any(part in EXCLUDED for part in path.relative_to(ROOT).parts): continue
            if path.suffix.lower() in (".pyc",".pyo",".log"): continue
            if path.is_symlink() or ROOT not in path.resolve().parents:
                raise ValueError("Refusing linked/outside file: "+str(path))
            files.append(path)
    for path in files:
        if not path.is_file(): raise FileNotFoundError(str(path))
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Refusing linked/outside file: "+str(path))
    return sorted(files,key=lambda p:p.relative_to(ROOT).as_posix())


def main():
    parser = argparse.ArgumentParser(description="导出不含本机数据的 GitHub 源码目录和 ZIP")
    parser.add_argument("--output",default=str(ROOT/"release-source"))
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    for source in DIRECTORIES:
        directory = ROOT/source
        if destination == directory or directory in destination.parents:
            parser.error("output must not be inside a source directory")
    destination.mkdir(parents=True,exist_ok=True)
    name = "StarMoonLauncher-"+__version__
    folder = destination/name
    archive = destination/(name+"-source.zip")
    if folder.exists() or archive.exists():
        parser.error("output already exists; choose a fresh --output directory")
    files = source_files()
    folder.mkdir()
    for path in files:
        target = folder/path.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(str(path),str(target))
    with zipfile.ZipFile(str(archive),"w",compression=zipfile.ZIP_DEFLATED) as output:
        for path in files:
            relative = path.relative_to(ROOT)
            output.write(str(folder/relative),str(Path(name)/relative))
    print("Source folder: "+str(folder))
    print("Source archive: "+str(archive))
    print("Files: {} / archive bytes: {}".format(len(files),archive.stat().st_size))


if __name__ == "__main__":
    main()
