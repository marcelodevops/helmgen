import subprocess
import sys
from pathlib import Path

def test_help_command():
    """Check that `helmgen --help` runs successfully and prints usage."""
    print("🧩 Testing helmgen --help ...")
    result = subprocess.run(
        [sys.executable, "-m", "helmgen", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Non-zero exit code: {result.returncode}"
    assert "usage" in result.stdout.lower(), "Help text not found in output"
    print("✅ helmgen --help works correctly.\n")

def test_cli_entrypoint():
    """Check that the installed CLI entrypoint works."""
    print("🧩 Testing installed CLI entrypoint `helmgen --help` ...")
    result = subprocess.run(
        ["helmgen", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Non-zero exit code: {result.returncode}"
    assert "usage" in result.stdout.lower(), "Help text not found in output"
    print("✅ CLI entrypoint works correctly.\n")

def test_compose_argument(tmp_path: Path):
    """Check that helmgen correctly handles a sample compose file argument."""
    tmp_path.mkdir(parents=True, exist_ok=True)   # <-- ensure directory exists
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "version: '3.9'\nservices:\n  app:\n    image: nginx:latest\n"
    )


    print("🧩 Testing helmgen with compose argument ...")
    result = subprocess.run(
        ["helmgen", str(compose_file), "--output", str(tmp_path / "chart")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Non-zero exit code: {result.returncode}"
    assert any(keyword in result.stdout.lower() for keyword in ["helm", "chart", "success"]), \
    f"Expected Helm chart generation message not found.\nOutput was:\n{result.stdout}"

    print("✅ helmgen handles compose argument correctly.\n")

if __name__ == "__main__":
    print("🚀 Running basic HelmGen CLI smoke tests...\n")
    test_help_command()
    test_cli_entrypoint()
    test_compose_argument(Path("./tmp_test"))
    print("🎉 All smoke tests passed!")
