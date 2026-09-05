"""Benchmark evaluating misclick rates on small targets: Coordinates vs Set-of-Marks refs.

As specified in docs/IMPLEMENTATION_PLAN.md Phase 11:
'misclick rate on a small-target benchmark (tests/desktop/benchmark_small_targets.py)
improves with Set-of-Marks enabled.'
"""

import random
from typing import Any

from local_control.core.actions import ClickAction, Rect
from local_control.core.coordinates import CoordinateMapper
from local_control.core.types import ImageRef, ScreenGeometry, UiElement
from local_control.execution.tools.base import ExecutionContext
from local_control.execution.tools.input_backend import FakeInputBackend
from local_control.execution.tools.input_tool import InputTool
from local_control.safety.kill_switch import StopToken


def run_benchmark(num_targets: int = 100, noise_std: float = 12.0) -> dict[str, Any]:
    """Simulate small-target clicking with coordinate jitter vs Set-of-Marks ref targeting."""
    # Setup screen and image geometry
    screen_geom = ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0)
    img_ref = ImageRef(
        path_original="",
        path_model="",
        model_width=1280,
        model_height=720,
        phash="0000000000000000",
    )
    mapper = CoordinateMapper(screen=screen_geom, image=img_ref)
    stop_token = StopToken()

    # Generate synthetic small UI elements (e.g. 18x18 px icons/buttons in model space)
    random.seed(42)
    elements: list[UiElement] = []
    for i in range(num_targets):
        x = random.randint(50, 1200)
        y = random.randint(50, 650)
        w = random.randint(14, 20)  # Small target dimensions
        h = random.randint(14, 20)
        elements.append(
            UiElement(
                ref=f"e{i + 1}",
                role="Button",
                name=f"Icon_{i + 1}",
                bbox=Rect(x=x, y=y, width=w, height=h),
                states=["enabled"],
            )
        )

    # 1. Benchmark: Coordinate-based targeting with vision model jitter
    coord_backend = FakeInputBackend()
    coord_tool = InputTool(backend=coord_backend)
    coord_ctx = ExecutionContext(
        run_id="bench_coord",
        stop=stop_token,
        mapper=mapper,
        ui_elements=elements,
    )

    coord_misses = 0
    for el in elements:
        # True center in model image space
        true_cx = el.bbox.x + el.bbox.width // 2
        true_cy = el.bbox.y + el.bbox.height // 2

        # Model visual prediction with typical spatial uncertainty noise
        predicted_x = round(true_cx + random.gauss(0, noise_std))
        predicted_y = round(true_cy + random.gauss(0, noise_std))

        coord_act = ClickAction(
            target_description=f"Click {el.name}",
            expected_outcome="Clicked",
            x=predicted_x,
            y=predicted_y,
        )
        resolved_pt, _ = coord_tool._resolve_point(coord_act, coord_ctx)

        # Check if the predicted point lands inside the element bbox
        in_bounds = (
            el.bbox.x <= resolved_pt.x < el.bbox.x + el.bbox.width
            and el.bbox.y <= resolved_pt.y < el.bbox.y + el.bbox.height
        )
        if not in_bounds:
            coord_misses += 1

    coord_misclick_rate = coord_misses / num_targets

    # 2. Benchmark: Set-of-Marks ref targeting
    som_backend = FakeInputBackend()
    som_tool = InputTool(backend=som_backend)
    som_ctx = ExecutionContext(
        run_id="bench_som",
        stop=stop_token,
        mapper=mapper,
        ui_elements=elements,
    )

    som_misses = 0
    for el in elements:
        action = ClickAction(
            target_description=f"Click {el.name}",
            expected_outcome="Clicked",
            ref=el.ref,
        )
        # InputTool resolves ref to element center
        model_pt, _ = som_tool._resolve_point(action, som_ctx)
        in_bounds = (
            el.bbox.x <= model_pt.x < el.bbox.x + el.bbox.width
            and el.bbox.y <= model_pt.y < el.bbox.y + el.bbox.height
        )
        if not in_bounds:
            som_misses += 1

    som_misclick_rate = som_misses / num_targets

    return {
        "num_targets": num_targets,
        "coordinate_misses": coord_misses,
        "coordinate_misclick_rate": coord_misclick_rate,
        "som_misses": som_misses,
        "som_misclick_rate": som_misclick_rate,
        "improvement_pct": (coord_misclick_rate - som_misclick_rate) * 100,
    }


def test_small_target_benchmark_accuracy() -> None:
    """Verify that Set-of-Marks ref targeting significantly reduces small-target misclicks."""
    results = run_benchmark(num_targets=100, noise_std=12.0)

    # Coordinate targeting with noise should have substantial misclick rate on small targets
    assert results["coordinate_misclick_rate"] > 0.35, (
        f"Expected noisy coordinates to miss >35% on small 16px targets, got {results['coordinate_misclick_rate']:.1%}"
    )

    # Set-of-Marks ref targeting should achieve 0% misclick rate
    assert results["som_misclick_rate"] == 0.0, (
        f"Expected 0% misclick with Set-of-Marks ref targeting, got {results['som_misclick_rate']:.1%}"
    )

    assert results["improvement_pct"] > 35.0


if __name__ == "__main__":
    res = run_benchmark()
    print("=" * 60)
    print("SMALL TARGET BENCHMARK RESULTS")
    print("=" * 60)
    print(f"Total Targets: {res['num_targets']}")
    print(f"Coordinate Misclick Rate (with vision jitter): {res['coordinate_misclick_rate']:.1%}")
    print(f"Set-of-Marks Misclick Rate (with ref targeting): {res['som_misclick_rate']:.1%}")
    print(f"Accuracy Improvement: +{res['improvement_pct']:.1f}%")
    print("=" * 60)
