import subprocess

def test_chart_generation(cli_path, sample_compose_file, temp_chart_dir):
    """Run helmgen with a sample compose file and validate generated chart files."""
    result = subprocess.run(
        cli_path + [str(sample_compose_file), "--output", str(temp_chart_dir)],
        capture_output=True,
        text=True,
    )

    # Confirm CLI executed successfully
    assert result.returncode == 0, f"Helmgen failed: {result.stderr}"
    print(result.stdout)

    # Expected files in the generated chart
    expected_files = [
        "Chart.yaml",
        "values.yaml",
        "templates/deployment.yaml",
        "templates/service.yaml",
    ]

    # Validate file existence
    for rel_path in expected_files:
        f = temp_chart_dir / rel_path
        assert f.exists(), f"Missing generated file: {f}"

    # Optional: Check placeholders in values.yaml
    values_yaml = (temp_chart_dir / "values.yaml").read_text()
    assert "replicaCount" in values_yaml or "image:" in values_yaml
