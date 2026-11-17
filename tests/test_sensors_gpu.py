"""GPU sensor against a fake /sys/class/drm tree (SP-0601).

The file contents are the ones read off a real RX 6700 XT (vendor 0x1002,
12 GiB VRAM) on the dev box.
"""
import pytest

from smartpanel.sensors import gpu

GIB = 1024 ** 3


def make_card(root, name="card1", *, vendor="0x1002", busy="37", used="1434300416",
              total="12868124672", temp="39000"):
    dev = root / name / "device"
    dev.mkdir(parents=True)
    if vendor is not None:
        (dev / "vendor").write_text(vendor + "\n")
    if busy is not None:
        (dev / "gpu_busy_percent").write_text(busy + "\n")
    if used is not None:
        (dev / "mem_info_vram_used").write_text(used + "\n")
    if total is not None:
        (dev / "mem_info_vram_total").write_text(total + "\n")
    if temp is not None:
        hw = dev / "hwmon" / "hwmon3"
        hw.mkdir(parents=True)
        (hw / "temp1_input").write_text(temp + "\n")
        (hw / "temp1_label").write_text("edge\n")
    return dev


@pytest.fixture(autouse=True)
def no_nvidia(monkeypatch):
    monkeypatch.setattr(gpu, "nvidia_gpu", lambda: None)


def test_amd_reading(tmp_path):
    make_card(tmp_path)
    (tmp_path / "card1-DP-1").mkdir()      # connectors sit next to the card
    (tmp_path / "renderD128").mkdir()
    out = gpu.gpu(tmp_path)
    assert out["AVAILABLE"] is True
    assert out["VENDOR"] == "amd"
    assert out["PERCENTAGE"] == 37.0
    assert out["MEMORY_TOTAL"] == pytest.approx(12868124672 / GIB)
    assert out["MEMORY_USED"] == pytest.approx(1434300416 / GIB)
    assert out["MEMORY_PERCENT"] == pytest.approx(1434300416 / 12868124672 * 100)
    assert out["TEMPERATURE"] == 39.0


def test_intel_card_without_busy_file_is_skipped(tmp_path):
    make_card(tmp_path, "card0", vendor="0x8086", busy=None, used=None, total=None,
              temp=None)
    out = gpu.gpu(tmp_path)
    assert out["AVAILABLE"] is False and out["VENDOR"] is None
    assert out["PERCENTAGE"] is None and out["TEMPERATURE"] is None


def test_amd_second_card_found_after_igpu(tmp_path):
    make_card(tmp_path, "card0", vendor="0x8086", busy="5")   # non-AMD with the file
    make_card(tmp_path, "card1", busy="80")
    out = gpu.gpu(tmp_path)
    assert out["VENDOR"] == "amd" and out["PERCENTAGE"] == 80.0


def test_amd_missing_vram_and_temp(tmp_path):
    make_card(tmp_path, used=None, total=None, temp=None)
    out = gpu.gpu(tmp_path)
    assert out["AVAILABLE"] is True
    assert out["MEMORY_USED"] is None and out["MEMORY_PERCENT"] is None
    assert out["TEMPERATURE"] is None


def test_garbage_and_missing_root(tmp_path):
    make_card(tmp_path, busy="n/a")
    assert gpu.gpu(tmp_path)["AVAILABLE"] is False
    assert gpu.gpu(tmp_path / "nope")["AVAILABLE"] is False


def test_nvidia_wins_when_present(tmp_path, monkeypatch):
    make_card(tmp_path)
    reading = gpu._empty() | {"AVAILABLE": True, "VENDOR": "nvidia", "PERCENTAGE": 12.0}
    monkeypatch.setattr(gpu, "nvidia_gpu", lambda: reading)
    assert gpu.gpu(tmp_path)["VENDOR"] == "nvidia"


def test_keys_are_stable(tmp_path):
    assert set(gpu.gpu(tmp_path)) == {"AVAILABLE", "VENDOR", "PERCENTAGE",
                                      "MEMORY_PERCENT", "MEMORY_USED",
                                      "MEMORY_TOTAL", "TEMPERATURE"}
