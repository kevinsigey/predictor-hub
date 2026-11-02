/**
 * Every kit block, with an empty model, a partial one and a full one.
 *
 * The kit has to survive a sport whose feed has not filled every part yet:
 * no graded week, no calibration buckets, no recent picks. Each of those is a
 * state a real page lands in on the day after a launch, and each one says so in
 * words rather than drawing a zero.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CalibrationBlock } from "./CalibrationBlock";
import { CalibrationChart } from "./CalibrationChart";
import { HowCounted } from "./HowCounted";
import { RecentPicks } from "./RecentPicks";
import { RecordBlock } from "./RecordBlock";
import { RecordHeadline } from "./RecordHeadline";
import { WeekStrip } from "./WeekStrip";
import type { CalibrationSeries, MarketTally, RecordModel } from "./types";

const FULL: RecordModel = {
  lead: "game_outcome",
  headline: "counted",
  markets: [
    { key: "game_outcome", label: "Winner pick", counted: { correct: 25, n: 52 }, preKickoff: { correct: 20, n: 40 } },
    { key: "spread", label: "Spread", counted: { correct: 18, n: 40 }, preKickoff: { correct: 15, n: 33 } },
  ],
  weeks: [
    { weekStart: "2026-09-01", n: 10, correct: 6, tracked: true },
    { weekStart: "2026-09-08", n: 8, correct: 3, tracked: true },
  ],
  calibration: [
    {
      key: "game_outcome",
      label: "Winner pick",
      buckets: [
        { bucket: "50-60%", n: 10, correct: 6 },
        { bucket: "70%+", n: 6, correct: 5 },
      ],
    },
  ],
  recent: [
    { id: "a", label: "Winner pick", pick: "Boston", actual: "Boston", hit: true, when: "Mon 5 Oct", madeBeforeKickoff: true },
    { id: "b", label: "Spread", pick: "Lakers −3.5", actual: "Celtics", hit: false, when: "Sun 4 Oct", madeBeforeKickoff: true },
  ],
};

describe("RecordHeadline", () => {
  it("shows the range the headline sample allows, not a bare rate", () => {
    render(<RecordHeadline markets={FULL.markets} mode="counted" lead="game_outcome" />);
    // 25 of 52, Wilson 95%: about 35% to 61%.
    expect(screen.getByText(/Could plausibly be 35%.61%/)).toBeInTheDocument();
  });

  it("leads with the percentage, the tally beneath it, and a rate bar over the range bar", () => {
    render(<RecordHeadline markets={FULL.markets} mode="counted" lead="game_outcome" />);
    // 25 of 52 = 48%: the rate is the big number, on its own hook, tally under it.
    expect(screen.getByTestId("rate-game_outcome")).toHaveTextContent("48%");
    expect(screen.getByText("25 of 52")).toBeInTheDocument();
    // Two bars on the lead line: the rate itself first, then the plausible range.
    const bars = screen.getAllByRole("img");
    expect(bars[0]).toHaveAccessibleName("Hit rate 48%");
    expect(bars[1]).toHaveAccessibleName(/Plausible range/);
  });

  it("counts every pick and puts the pre-kickoff figure beside it", () => {
    render(<RecordHeadline markets={FULL.markets} mode="counted" lead="game_outcome" moment="tip-off" />);
    expect(screen.getByText("20 of 40 made before tip-off")).toBeInTheDocument();
  });

  it("leads with the pre-kickoff subset when that is the mode", () => {
    const { container } = render(<RecordHeadline markets={FULL.markets} mode="preKickoff" lead="game_outcome" />);
    // 20 of 40 is the big number, and no "beside" line is drawn.
    expect(container.textContent).toMatch(/20\s*of\s*40/);
    expect(container.textContent).not.toMatch(/made before/);
  });

  it("renders nothing at all when no market has a tally", () => {
    const { container } = render(<RecordHeadline markets={[]} mode="counted" lead="" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("WeekStrip", () => {
  it("draws one cell per graded week with an accessible label", () => {
    render(<WeekStrip weeks={FULL.weeks!} />);
    expect(screen.getByRole("list", { name: "Hit rate by week" })).toBeInTheDocument();
    expect(screen.getAllByTestId("week-cell")).toHaveLength(2);
  });

  it("says so when no week has been graded", () => {
    render(<WeekStrip weeks={[]} />);
    expect(screen.getByText("No graded week yet.")).toBeInTheDocument();
  });
});

describe("CalibrationChart", () => {
  it("draws the buckets as points on a labelled image", () => {
    render(<CalibrationChart series={FULL.calibration!} />);
    expect(screen.getByRole("img", { name: "Calibration for Winner pick" })).toBeInTheDocument();
    expect(screen.getAllByTestId("calibration-point")).toHaveLength(2);
  });

  it("shows each market's settled-pick count on its pill", () => {
    const twoMarkets: CalibrationSeries[] = [
      { key: "game_outcome", label: "Winner pick", buckets: [{ bucket: "50-60%", n: 10, correct: 6 }] },
      { key: "spread", label: "Spread", buckets: [{ bucket: "50-60%", n: 14, correct: 7 }] },
    ];
    const twoMarketTallies: MarketTally[] = [
      { key: "game_outcome", label: "Winner pick", counted: { correct: 25, n: 52 } },
      { key: "spread", label: "Spread", counted: { correct: 18, n: 40 } },
    ];
    render(<CalibrationChart series={twoMarkets} markets={twoMarketTallies} />);
    const buttons = screen.getAllByRole("button");
    // Winner pick counted.n = 52.
    const winner = buttons.find((b) => b.textContent?.includes("Winner pick"));
    expect(winner).toBeDefined();
    expect(winner!.textContent).toContain("52 settled");
    // Spread counted.n = 40.
    const spread = buttons.find((b) => b.textContent?.includes("Spread"));
    expect(spread).toBeDefined();
    expect(spread!.textContent).toContain("40 settled");
  });

  it("says Too early when the market has fewer than 3 settled picks", () => {
    const series = [{ key: "moneyline", label: "Moneyline", buckets: [{ bucket: "50-60%", n: 2, correct: 1 }] }];
    render(<CalibrationChart series={series} />);
    expect(screen.getByText(/Too early to calibrate Moneyline/)).toBeInTheDocument();
    expect(screen.queryByTestId("calibration-point")).toBeNull();
  });

  it("draws 1:1 in the width it is given, so its text is readable on a phone and the chart is tall enough on desktop", () => {
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect");
    try {
      rect.mockReturnValue({ width: 343, height: 0, x: 0, y: 0, top: 0, left: 0, right: 343, bottom: 0, toJSON: () => ({}) } as DOMRect);
      const { container, unmount } = render(<CalibrationChart series={FULL.calibration!} markets={FULL.markets} />);
      const svg = container.querySelector("svg")!;
      expect(svg.getAttribute("viewBox")).toBe("0 0 343 280");     // the phone: viewBox width = pixel width, not 960 scaled down
      expect(svg.className.baseVal).not.toMatch(/max-w-(md|sm|xs)/);
      unmount();

      rect.mockReturnValue({ width: 1200, height: 0, x: 0, y: 0, top: 0, left: 0, right: 1200, bottom: 0, toJSON: () => ({}) } as DOMRect);
      const wide = render(<CalibrationChart series={FULL.calibration!} markets={FULL.markets} />);
      expect(wide.container.querySelector("svg")!.getAttribute("viewBox")).toBe("0 0 1200 480");   // the hero: bigger than before
    } finally {
      rect.mockRestore();
    }
  });

  it("the chosen market's pill is drawn on the accent fill, not stacked with the panel fill", () => {
    const series: CalibrationSeries[] = [
      { key: "a", label: "Winner", buckets: [{ bucket: "50-60%", n: 12, correct: 7 }] },
      { key: "b", label: "Spread", buckets: [{ bucket: "50-60%", n: 12, correct: 7 }] },
    ];
    render(<CalibrationChart series={series} />);
    const active = screen.getByRole("button", { name: /Winner/, pressed: true });
    expect(active.className).toMatch(/bg-pr-accent/);
    expect(active.className).not.toMatch(/bg-pr-panel/);      // two competing backgrounds left dark text on a dark pill
    expect(screen.getByRole("button", { name: /Spread/ }).className).toMatch(/bg-pr-panel-2/);
  });

  it("opens on a market that has enough settled picks to draw", () => {
    const series: CalibrationSeries[] = [
      { key: "a", label: "Thin market", buckets: [{ bucket: "50-60%", n: 2, correct: 1 }] },
      { key: "b", label: "Full market", buckets: [{ bucket: "50-60%", n: 12, correct: 7 }] },
    ];
    render(<CalibrationChart series={series} />);
    expect(screen.getByRole("img", { name: "Calibration for Full market" })).toBeInTheDocument();
  });

  it("says so when nothing is graded", () => {
    render(<CalibrationChart series={[]} />);
    expect(screen.getByText("No confidence buckets graded yet.")).toBeInTheDocument();
  });
});

describe("CalibrationBlock", () => {
  it("wraps the chart in the titled record block", () => {
    const series = [{ key: "moneyline", label: "Moneyline", buckets: [{ bucket: "50-60%", n: 10, correct: 6 }] }];
    render(<CalibrationBlock series={series} />);
    expect(screen.getByRole("region", { name: "Does 70% mean 70%?" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Calibration for Moneyline" })).toBeInTheDocument();
  });
});

describe("RecentPicks", () => {
  it("reads each pick as Right or Wrong, in words", () => {
    render(<RecentPicks picks={FULL.recent!} />);
    expect(screen.getAllByTestId("recent-pick")).toHaveLength(2);
    expect(screen.getByText("Right")).toBeInTheDocument();
    expect(screen.getByText("Wrong")).toBeInTheDocument();
  });

  it("labels a rebuilt pick with when it was made", () => {
    render(
      <RecentPicks
        picks={[{ id: "c", label: "Winner pick", pick: "Denver", actual: "Denver", hit: true, when: "Sat 3 Oct", madeBeforeKickoff: false }]}
        moment="tip-off"
      />,
    );
    expect(screen.getByText(/rebuilt after tip-off/)).toBeInTheDocument();
  });

  it("says so when nothing is graded", () => {
    render(<RecentPicks picks={[]} />);
    expect(screen.getByText("No counted pick has been graded yet.")).toBeInTheDocument();
  });
});

describe("HowCounted", () => {
  it("is collapsed, and states the rule once", () => {
    render(<HowCounted moment="tip-off" headline="preKickoff" />);
    const details = screen.getByText("How this is counted").closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(details!.textContent).toMatch(/earliest recorded/);
    expect(details!.textContent).toMatch(/before tip-off/);
    // The superseded exclusion must not come back through this block.
    expect(details!.textContent).not.toMatch(/not\s+counted/i);
  });

  it("shows the as-of stamp, formatted from the raw ISO timestamp", () => {
    render(<HowCounted moment="kickoff" headline="counted" asOf="2026-11-02T16:15:00Z" />);
    const details = screen.getByText("How this is counted").closest("details");
    expect(details!.textContent).toMatch(/Record as of .+2026/);
  });

  it("renders no stamp for an unparseable or absent as-of", () => {
    const { rerender } = render(<HowCounted moment="kickoff" headline="counted" asOf="not a date" />);
    expect(screen.getByText("How this is counted").closest("details")!.textContent).not.toMatch(/Record as of/);
    rerender(<HowCounted moment="kickoff" headline="counted" />);
    expect(screen.getByText("How this is counted").closest("details")!.textContent).not.toMatch(/Record as of/);
  });
});

describe("RecordBlock", () => {
  it("labels its region from its own heading", () => {
    render(
      <RecordBlock id="tr-week" title="Week by week" caption="One cell per week.">
        <p>body</p>
      </RecordBlock>,
    );
    expect(screen.getByRole("region")).toHaveAccessibleName("Week by week");
    expect(screen.getByText("One cell per week.")).toBeInTheDocument();
  });
});

describe("the whole kit speaks one vocabulary", () => {
  it("never renders Called it, Missed, ats, totals, hit or miss", () => {
    const { container } = render(
      <div>
        <RecordHeadline markets={FULL.markets} mode="counted" lead="game_outcome" />
        <WeekStrip weeks={FULL.weeks!} />
        <CalibrationChart series={FULL.calibration!} />
        <RecentPicks picks={FULL.recent!} />
        <HowCounted moment="kickoff" headline="counted" />
      </div>,
    );
    expect(container.textContent).not.toMatch(/called it/i);
    expect(container.textContent).not.toMatch(/\bmissed\b/i);
    expect(container.textContent).not.toMatch(/\bats\b/i);
    expect(container.textContent).not.toMatch(/\btotals\b/i);
    expect(container.textContent).not.toMatch(/\bhits?\b/i);
    expect(container.textContent).not.toMatch(/\bmiss(es)?\b/i);
  });
});
