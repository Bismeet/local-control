from datetime import UTC, datetime

from local_control.config.settings import Settings
from local_control.core.actions import (
    BrowserBackAction,
    BrowserClickAction,
    BrowserDownloadAction,
    BrowserNavigateAction,
    BrowserReadAction,
    BrowserSnapshotAction,
    BrowserTabsAction,
    BrowserTypeAction,
)
from local_control.core.types import (
    BrowserObservation,
    ImageRef,
    Observation,
    Point,
    ScreenGeometry,
)
from local_control.safety.policy import classify


def make_obs(
    url: str = "http://127.0.0.1:8000/app",
    snapshot: str = "",
) -> Observation:
    """Helper to create dummy observation with browser context."""
    return Observation(
        step_index=1,
        captured_at=datetime.now(UTC),
        screen=ScreenGeometry(width_px=1920, height_px=1080, scale_factor=1.0),
        image=ImageRef(
            path_original="",
            path_model="",
            model_width=1280,
            model_height=720,
            phash="0000000000000000",
        ),
        screen_state="normal",
        foreground=None,
        windows=[],
        cursor=Point(x=100, y=100),
        browser=BrowserObservation(
            url=url,
            title="Test Page",
            snapshot=snapshot,
            tabs=[],
            active_tab_index=0,
            tab_count=1,
            is_agent_browser_foreground=True,
        ),
    )


def test_b03_blocked_password_input():
    # Selector contains password
    act1 = BrowserTypeAction(
        selector="input[type=password]",
        text="secret",
        target_description="Type password",
        expected_outcome="Password entered",
    )
    tier, cat, _, _, _ = classify(act1)
    assert tier == "BLOCKED"
    assert cat == "B-03"

    # Target description mentions password
    act2 = BrowserTypeAction(
        ref="e1",
        text="secret",
        target_description="Enter user password",
        expected_outcome="Credentials provided",
    )
    tier, cat, _, _, _ = classify(act2)
    assert tier == "BLOCKED"
    assert cat == "B-03"

    # Snapshot ref has type=password
    obs = make_obs(snapshot='[e2] textbox "PIN" (type=password)')
    act3 = BrowserTypeAction(
        ref="e2",
        text="1234",
        target_description="Enter PIN",
        expected_outcome="PIN filled",
    )
    tier, cat, _, _, _ = classify(act3, obs=obs)
    assert tier == "BLOCKED"
    assert cat == "B-03"

    # Snapshot ref has credit card number
    obs_card = make_obs(snapshot='[e3] textbox "Card Number"')
    act4 = BrowserTypeAction(
        ref="e3",
        text="4111222233334444",
        target_description="Input number",
        expected_outcome="Field filled",
    )
    tier, cat, _, _, _ = classify(act4, obs=obs_card)
    assert tier == "BLOCKED"
    assert cat == "B-03"


def test_b04_blocked_payment_intent():
    # Target description mentions Pay now
    act1 = BrowserClickAction(
        ref="b1",
        target_description="Click Pay now button",
        expected_outcome="Payment initiated",
    )
    tier, cat, _, _, _ = classify(act1)
    assert tier == "BLOCKED"
    assert cat == "B-04"

    # Expected outcome mentions checkout
    act2 = BrowserClickAction(
        ref="b2",
        target_description="Click button",
        expected_outcome="Complete checkout",
    )
    tier, cat, _, _, _ = classify(act2)
    assert tier == "BLOCKED"
    assert cat == "B-04"

    # Snapshot ref matches payment text
    obs = make_obs(snapshot='[b3] button "Pay now"\n[b4] button "Upgrade and Pay now"')
    act3 = BrowserClickAction(
        ref="b3",
        target_description="Click button",
        expected_outcome="Next step",
    )
    tier, cat, _, _, _ = classify(act3, obs=obs)
    assert tier == "BLOCKED"
    assert cat == "B-04"

    act4 = BrowserClickAction(
        ref="b4",
        target_description="Click upgrade",
        expected_outcome="Account updated",
    )
    tier, cat, _, _, _ = classify(act4, obs=obs)
    assert tier == "BLOCKED"
    assert cat == "B-04"

    # URL is payment domain
    obs_stripe = make_obs(url="https://checkout.stripe.com/pay/cs_test_123")
    act5 = BrowserClickAction(
        ref="any_button",
        target_description="Click continue",
        expected_outcome="Proceed",
    )
    tier, cat, _, _, _ = classify(act5, obs=obs_stripe)
    assert tier == "BLOCKED"
    assert cat == "B-04"


def test_b13_blocked_browser_schemes():
    schemes = [
        "file:///C:/Windows/System32",
        "chrome://settings",
        "edge://flags",
        "javascript:alert(1)",
        "data:text/html,dangerous",
        "about:config",
    ]
    for url in schemes:
        act = BrowserNavigateAction(
            url=url,
            target_description="Navigate URL",
            expected_outcome="Page loaded",
        )
        tier, cat, _, _, _ = classify(act)
        assert tier == "BLOCKED", f"URL {url} should be blocked"
        assert cat == "B-13"

    # about:blank is permitted
    act_blank = BrowserNavigateAction(
        url="about:blank",
        target_description="Navigate to blank",
        expected_outcome="Blank page",
    )
    tier, cat, _, _, _ = classify(act_blank)
    assert tier == "SAFE"
    assert cat == "S-07"


def test_c08_confirm_submit():
    # submit=True on BrowserTypeAction
    act1 = BrowserTypeAction(
        selector="input[name=q]",
        text="query",
        submit=True,
        target_description="Type and enter",
        expected_outcome="Form submitted",
    )
    tier, cat, _, _, _ = classify(act1)
    assert tier == "CONFIRM"
    assert cat == "C-08"

    # Click Submit button by target description
    act2 = BrowserClickAction(
        ref="s1",
        target_description="Click Submit button",
        expected_outcome="Support ticket submitted",
    )
    tier, cat, _, _, _ = classify(act2)
    assert tier == "CONFIRM"
    assert cat == "C-08"

    # Click Submit button by selector
    act3 = BrowserClickAction(
        selector="button[type=submit]",
        target_description="Click action button",
        expected_outcome="Form sent",
    )
    tier, cat, _, _, _ = classify(act3)
    assert tier == "CONFIRM"
    assert cat == "C-08"

    # Click Submit button by snapshot text
    obs = make_obs(snapshot='[s4] button "Submit request"\n[s5] button "Send message"')
    act4 = BrowserClickAction(
        ref="s4",
        target_description="Click option",
        expected_outcome="Request sent",
    )
    tier, cat, _, _, _ = classify(act4, obs=obs)
    assert tier == "CONFIRM"
    assert cat == "C-08"

    act5 = BrowserClickAction(
        ref="s5",
        target_description="Click message",
        expected_outcome="Sent",
    )
    tier, cat, _, _, _ = classify(act5, obs=obs)
    assert tier == "CONFIRM"
    assert cat == "C-08"


def test_c12_confirm_download():
    act = BrowserDownloadAction(
        dest_dir="C:/downloads",
        ref="d1",
        target_description="Download file",
        expected_outcome="File saved",
    )
    tier, cat, _, grantable, _ = classify(act)
    assert tier == "CONFIRM"
    assert cat == "C-12"
    assert grantable is True


def test_c13_confirm_new_hosts():
    settings = Settings()
    settings.safety.confirm_new_hosts = True
    settings.safety.seen_hosts = {"example.com"}

    # Already seen host -> SAFE
    act_seen = BrowserNavigateAction(
        url="https://example.com/page",
        target_description="Navigate known site",
        expected_outcome="Loaded",
    )
    tier, cat, _, _, _ = classify(act_seen, settings=settings)
    assert tier == "SAFE"

    # New host -> CONFIRM
    act_new = BrowserNavigateAction(
        url="https://unknown-domain.org/index",
        target_description="Navigate unknown site",
        expected_outcome="Loaded",
    )
    tier, cat, _, grantable, _ = classify(act_new, settings=settings)
    assert tier == "CONFIRM"
    assert cat == "C-13"
    assert grantable is True


def test_s07_safe_browser_actions():
    obs = make_obs(snapshot='[e1] link "Products"\n[e2] textbox "Search"')

    act_nav = BrowserNavigateAction(
        url="http://127.0.0.1:8000/home",
        target_description="Go home",
        expected_outcome="Home page loaded",
    )
    assert classify(act_nav)[0] == "SAFE"

    act_read = BrowserReadAction(
        target_description="Read content",
        expected_outcome="Text extracted",
    )
    assert classify(act_read)[0] == "SAFE"

    act_snap = BrowserSnapshotAction(
        target_description="Take snapshot",
        expected_outcome="DOM snapshot",
    )
    assert classify(act_snap)[0] == "SAFE"

    act_back = BrowserBackAction(
        target_description="Go back",
        expected_outcome="Previous page",
    )
    assert classify(act_back)[0] == "SAFE"

    act_tabs = BrowserTabsAction(
        op="list",
        target_description="List tabs",
        expected_outcome="Tabs listed",
    )
    assert classify(act_tabs)[0] == "SAFE"

    act_click = BrowserClickAction(
        ref="e1",
        target_description="Click products link",
        expected_outcome="Product catalog opened",
    )
    assert classify(act_click, obs=obs)[0] == "SAFE"

    act_type = BrowserTypeAction(
        ref="e2",
        text="laptop",
        target_description="Type query",
        expected_outcome="Query entered",
    )
    assert classify(act_type, obs=obs)[0] == "SAFE"
