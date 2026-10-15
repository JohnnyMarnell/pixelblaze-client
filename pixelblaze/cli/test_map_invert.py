#!/usr/bin/env python3
"""Unit tests for `pb map --invert` / `--axis`. No Pixelblaze hardware needed.

The flip reflects each coordinate about the MIDPOINT of its axis's own
extent, v -> (min + max) - v. Midpoint rather than centre of mass matters
because createMapData rescales every axis by its own min/max on the way to
the device: a centroid reflection of an unevenly spread fixture lands outside
the extent it had, and the fixture would come back a different size than it
went in. The first test below is the one that pins that.
"""

import pytest
import click

from pixelblaze.cli.cli import _invert_coordinates, _parse_axes, _AXIS_NAMES


# A deliberately lopsided map: most pixels bunched low on y, one up high. Its
# centre of mass and the midpoint of its extent are in different places.
LOPSIDED = [[0.0, 0.0], [0.1, 0.1], [0.2, 0.1], [0.3, 0.2], [0.4, 1.0]]


def _axis(coords, d):
    return [c[d] for c in coords]


def test_reflects_about_the_midpoint_and_keeps_the_extent():
    flipped = _invert_coordinates(LOPSIDED, [1])
    assert min(_axis(flipped, 1)) == min(_axis(LOPSIDED, 1))
    assert max(_axis(flipped, 1)) == max(_axis(LOPSIDED, 1))
    # Lowest becomes highest, exactly.
    assert flipped[0][1] == pytest.approx(1.0)
    assert flipped[4][1] == pytest.approx(0.0)


def test_a_centroid_flip_would_have_left_the_extent_and_this_does_not():
    """Why the midpoint and not the centre of mass.

    These pixels are bunched low on y with one outlier at the top, so the
    centroid (0.28) sits below the midpoint (0.5) and reflecting about it
    overshoots the BOTTOM of the fixture — the top LED lands at -0.44, a
    position the fixture never occupied. createMapData would then rescale
    the axis over that wider range and the whole thing would come back
    squashed. Reflecting about the midpoint cannot leave the extent.
    """
    lo, hi = min(_axis(LOPSIDED, 1)), max(_axis(LOPSIDED, 1))
    mean = sum(_axis(LOPSIDED, 1)) / len(LOPSIDED)
    centroid_flipped = [2 * mean - v for v in _axis(LOPSIDED, 1)]
    assert min(centroid_flipped) < lo                      # outside the fixture
    assert min(centroid_flipped) == pytest.approx(-0.44)

    midpoint_flipped = _axis(_invert_coordinates(LOPSIDED, [1]), 1)
    assert min(midpoint_flipped) == pytest.approx(lo)
    assert max(midpoint_flipped) == pytest.approx(hi)


def test_leaves_the_other_axes_alone():
    flipped = _invert_coordinates(LOPSIDED, [1])
    assert _axis(flipped, 0) == _axis(LOPSIDED, 0)


def test_is_its_own_undo():
    there_and_back = _invert_coordinates(_invert_coordinates(LOPSIDED, [0, 1]), [0, 1])
    for original, roundtrip in zip(LOPSIDED, there_and_back):
        assert roundtrip == pytest.approx(original)


def test_does_not_mutate_its_input():
    before = [list(p) for p in LOPSIDED]
    _invert_coordinates(LOPSIDED, [0, 1])
    assert LOPSIDED == before


def test_handles_an_empty_map():
    assert _invert_coordinates([], [1]) == []


def test_default_axis_is_the_vertical_or_the_only_one_there_is():
    assert _parse_axes(None, 3) == [1]      # y
    assert _parse_axes(None, 2) == [1]      # y
    assert _parse_axes(None, 1) == [0]      # x — a 1D map has nothing else


def test_reads_the_spellings_people_will_type():
    assert _parse_axes('x', 3) == [0]
    assert _parse_axes('Z', 3) == [2]
    assert _parse_axes('x,z', 3) == [0, 2]
    assert _parse_axes('x z', 3) == [0, 2]
    assert _parse_axes('all', 3) == [0, 1, 2]
    assert _parse_axes('all', 2) == [0, 1]
    assert _parse_axes('y,y', 3) == [1]     # a repeat is not two flips


def test_refuses_an_axis_that_is_not_one():
    with pytest.raises(click.ClickException, match="takes x, y, z or all"):
        _parse_axes('w', 3)


def test_refuses_an_axis_the_map_does_not_have():
    with pytest.raises(click.ClickException, match="it has no z"):
        _parse_axes('z', 2)


def test_three_dimensions_flip_independently():
    cube = [[0.0, 0.0, 0.0], [1.0, 0.5, 0.25], [0.5, 1.0, 1.0]]
    assert _invert_coordinates(cube, [2]) == [
        [0.0, 0.0, 1.0], [1.0, 0.5, 0.75], [0.5, 1.0, 0.0],
    ]
    assert _invert_coordinates(cube, [0, 1, 2]) == [
        [1.0, 1.0, 1.0], [0.0, 0.5, 0.75], [0.5, 0.0, 0.0],
    ]


def test_axis_names_are_the_order_the_wire_uses():
    assert _AXIS_NAMES == ('x', 'y', 'z')


# ── the command itself ────────────────────────────────────────────────────
# What a test of _invert_coordinates can't reach: that `pb map --invert` with
# nothing to set reads the device's map and writes the flip back, and that
# asking for it as CSV prints instead of writing.

class FakeDevice:
    """Just the four methods `pb map` uses, and a record of what was set."""

    def __init__(self, per_axis):
        self._per_axis = per_axis          # [[x...], [y...], [z...]], as the device answers
        self.written = None

    def getMapCoordinates(self):
        return self._per_axis

    def getPixelCount(self):
        return len(self._per_axis[0])

    def setMapCoordinates(self, coords):
        self.written = coords
        return True

    def getMapFunction(self):
        return None


@pytest.fixture
def device(monkeypatch):
    """A 4-pixel 2D map: y runs 0, 0.25, 0.5, 1.0."""
    from contextlib import contextmanager
    from pixelblaze.cli import cli_utils

    fake = FakeDevice([[0.0, 0.3, 0.6, 1.0], [0.0, 0.25, 0.5, 1.0]])

    @contextmanager
    def _fake_get(ctx):
        yield fake

    monkeypatch.setattr(cli_utils, 'get_pixelblaze', _fake_get)
    return fake


def _run(args, stdin=''):
    from click.testing import CliRunner
    from pixelblaze.cli.cli import pixelblaze
    return CliRunner().invoke(pixelblaze, ['--ip', '127.0.0.1', 'map'] + args, input=stdin)


def test_invert_with_no_input_flips_what_is_on_the_device(device):
    result = _run(['--invert'])
    assert result.exit_code == 0, result.output
    # y reflected about its own midpoint; x untouched.
    assert [p[1] for p in device.written] == pytest.approx([1.0, 0.75, 0.5, 0.0])
    assert [p[0] for p in device.written] == pytest.approx([0.0, 0.3, 0.6, 1.0])


def test_axis_picks_the_axis_and_implies_invert(device):
    assert _run(['--axis', 'x']).exit_code == 0
    assert [p[0] for p in device.written] == pytest.approx([1.0, 0.7, 0.4, 0.0])
    assert [p[1] for p in device.written] == pytest.approx([0.0, 0.25, 0.5, 1.0])


def test_csv_with_invert_prints_and_writes_nothing(device):
    result = _run(['--csv', '--invert'])
    assert result.exit_code == 0, result.output
    assert device.written is None, "a read command must not write"
    rows = [r for r in result.output.strip().splitlines() if ',' in r]
    assert rows[0] == 'index,x,y'
    assert rows[1].endswith(',1.0')       # first pixel's y flipped to the top
    assert 'not written' in result.output


def test_setting_a_csv_map_can_flip_it_on_the_way_in(device):
    result = _run(['--csv', '--invert'], stdin='x,y\n0,0\n0,1\n')
    assert result.exit_code == 0, result.output
    assert device.written == [[0.0, 1.0], [0.0, 0.0]]


def test_clear_and_invert_are_refused_together(device):
    result = _run(['--clear', '--invert'])
    assert result.exit_code != 0
    assert device.written is None
