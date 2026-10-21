from arch_gui_installer.storage.fstab import without_live_swap


def test_live_swap_file_and_device_are_excluded():
    raw = "# root\nUUID=root / ext4 defaults 0 1\n/swap/swapfile none swap defaults 0 0\nUUID=live-swap none swap defaults 0 0\nUUID=boot /boot vfat defaults 0 2\n"
    cleaned = without_live_swap(raw)
    assert "/swap/swapfile" not in cleaned
    assert "live-swap" not in cleaned
    assert "UUID=root / ext4" in cleaned
    assert "UUID=boot /boot vfat" in cleaned
