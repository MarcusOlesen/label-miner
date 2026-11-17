"""Where a line of text sits in a photo, and the direction it runs in.

Shared by the OCR (ocr.py) and the label designs (designs.py), which pick
out a label's lines by their position.
"""

import math
from dataclasses import dataclass, field

# Lines of one label run within 6 degrees of each other in every photo
# measured, while tilted print on the box nearby runs 12 degrees or more off
PARALLEL_LIMIT = 10


@dataclass
class TextBlock:
    """One line of OCR'd text and where it sits in the photo, in pixels from
    the top-left corner."""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    # The direction the line runs in, in degrees from -90 to 90: 0 is level,
    # positive slopes down to the right, +-90 is sideways
    direction: float = 0.0
    # The corner points of the text itself, which follow it when it is
    # tilted (empty: the upright box's corners)
    corners: list = field(default_factory=list)

    @property
    def points(self) -> list:
        if self.corners:
            return self.corners
        return [
            (self.left, self.top),
            (self.right, self.top),
            (self.right, self.bottom),
            (self.left, self.bottom),
        ]

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def direction_of(points) -> float:
    """The direction of the longer side of a piece's box (see
    TextBlock.direction). RapidOCR doesn't always start its corner points
    where the text starts."""
    (x0, y0), (x1, y1), (x2, y2) = points[:3]
    if math.hypot(x1 - x0, y1 - y0) < math.hypot(x2 - x1, y2 - y1):
        (x0, y0), (x1, y1) = (x1, y1), (x2, y2)
    return folded(math.degrees(math.atan2(y1 - y0, x1 - x0)))


def folded(direction: float) -> float:
    """The same direction between -90 and 90 degrees, so it points right."""
    return (direction + 90) % 180 - 90


def parallel(direction: float, other: float) -> bool:
    difference = abs(direction - other) % 180
    return min(difference, 180 - difference) <= PARALLEL_LIMIT


def axes(direction: float):
    """Unit vectors along a line running in this direction, and across it."""
    radians = math.radians(direction)
    return (math.cos(radians), math.sin(radians)), (-math.sin(radians), math.cos(radians))


def dot(point, axis) -> float:
    return point[0] * axis[0] + point[1] * axis[1]


def span(points, axis) -> tuple[float, float]:
    """Where the points start and end along an axis."""
    values = [dot(point, axis) for point in points]
    return min(values), max(values)


def turned_clockwise(block: TextBlock) -> TextBlock:
    """The block as it sits in the photo turned a quarter turn clockwise:
    right becomes down. Only positions relative to other blocks matter, so
    the photo's own width isn't needed (the turned block is shifted by it)."""
    return TextBlock(
        block.text,
        -block.bottom,
        block.left,
        -block.top,
        block.right,
        folded(block.direction + 90),
        [(-y, x) for x, y in block.corners],
    )
