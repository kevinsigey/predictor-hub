import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProbabilityBar } from "./ProbabilityBar";
import { MatchCard } from "./MatchCard";

describe("ProbabilityBar", () => {
  it("labels every segment in the given order, in words and for assistive tech", () => {
    render(<ProbabilityBar segments={[{ label: "Home", prob: 0.36 }, { label: "Draw", prob: 0.26 }, { label: "Away", prob: 0.38 }]} />);
    const bar = screen.getByRole("img");
    expect(bar).toHaveAccessibleName("Home 36%, Draw 26%, Away 38%");
    const labels = screen.getAllByTestId("pbar-label").map((n) => n.textContent);
    expect(labels).toEqual(["Home 36%", "Draw 26%", "Away 38%"]);
  });
  it("keeps a two-way bar in the header's order (away first for US sports)", () => {
    render(<ProbabilityBar segments={[{ label: "KC", prob: 0.38, color: "#E31837" }, { label: "BAL", prob: 0.62, color: "#241773" }]} />);
    expect(screen.getAllByTestId("pbar-label").map((n) => n.textContent)).toEqual(["KC 38%", "BAL 62%"]);
  });
});

const base = {
  left: { code: "TOT", name: "Tottenham" },
  right: { code: "AVL", name: "Aston Villa" },
  centre: "2–3",
  status: "called" as const,
  onOpen: () => {},
};

describe("MatchCard", () => {
  it("states one pick with its confidence", () => {
    render(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.38 }} />);
    expect(screen.getByText("Pick: Aston Villa win · 38%")).toBeInTheDocument();
    expect(screen.getByText("Called it ✓")).toBeInTheDocument();
  });
  it("headlines the market line with the model beside it, and says so when there is no line", () => {
    const { rerender } = render(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.38 }} market={{ label: "Tottenham win", prob: 0.41 }} />);
    expect(screen.getByTestId("market-headline")).toHaveTextContent("Market: Tottenham win · 41%");
    expect(screen.getByText("Model: Aston Villa win · 38%")).toBeInTheDocument();
    rerender(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.38 }} market={null} />);
    expect(screen.queryByTestId("market-headline")).toBeNull();
    expect(screen.getByText("Pick: Aston Villa win · 38%")).toBeInTheDocument();
    expect(screen.getByText("No market line")).toBeInTheDocument();
  });
  it("says 'No pick yet' without a pick, and shows exactly one status", () => {
    render(<MatchCard {...base} status="nopick" />);
    expect(screen.getAllByText(/Next up|Live|Called it|Missed|No pick yet|Made after kickoff/)).toHaveLength(1);
    expect(screen.getByText("No pick yet")).toBeInTheDocument();
  });
  it("is a real button: Enter and Space both open it, and its name summarises the pick", async () => {
    const onOpen = vi.fn();
    render(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.38 }} onOpen={onOpen} />);
    const card = screen.getByRole("button");
    // Named from its visible content (WCAG 2.5.3), so nothing on the card is hidden from screen readers.
    expect(card).not.toHaveAttribute("aria-label");
    expect(card).toHaveAccessibleName(/Tottenham.*2–3.*Aston Villa.*Pick: Aston Villa win · 38%/);
    expect(card.querySelector("div")).toBeNull();
    card.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
  it("shows sport meta such as a spread line", () => {
    render(<MatchCard {...base} status="next" meta="KC −3.5 · Total 46.5" />);
    expect(screen.getByText("KC −3.5 · Total 46.5")).toBeInTheDocument();
  });
});

describe("readability of team colours on the dark panel", () => {
  it("swaps a team colour that would vanish on the panel for a visible neutral", () => {
    const { container } = render(<ProbabilityBar segments={[{ label: "PIT", prob: 0.45, color: "#FFB612" }, { label: "CLE", prob: 0.55, color: "#311D00" }]} />);
    const fills = container.querySelectorAll<HTMLElement>("[data-testid='pbar-fill']");
    expect(fills[0].style.backgroundColor).toBe("rgb(255, 182, 18)");
    expect(fills[1].style.backgroundColor).not.toBe("rgb(49, 29, 0)");
  });
});

describe("MatchCard without a pick", () => {
  it("has no empty pick section", () => {
    const { container, rerender } = render(<MatchCard {...base} status="nopick" />);
    expect(container.querySelector("[data-testid='pick-section']")).toBeNull();
    rerender(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.38 }} />);
    expect(container.querySelector("[data-testid='pick-section']")).not.toBeNull();
  });
});

describe("fallback fills", () => {
  it("gives home, draw and away three different fills when no team colours are known", () => {
    // The palette under this changed and the assertion still means what it said:
    // three distinguishable tones. It used to be accent / faint / dim, with the
    // accent going to whichever segment was listed first — which claimed a pick
    // the bar was never told about. With no pick there is no accent to spend, so
    // the three tones are now the neutral ramp: the assertion, not the values.
    const { container } = render(<ProbabilityBar segments={[{ label: "Home", prob: 0.4 }, { label: "Draw", prob: 0.3 }, { label: "Away", prob: 0.3 }]} />);
    const fills = [...container.querySelectorAll<HTMLElement>("[data-testid='pbar-fill']")].map((f) => f.style.backgroundColor);
    expect(new Set(fills).size).toBe(3);
  });
});

describe("the match card's bar", () => {
  const bar = [{ label: "TOT", prob: 0.4 }, { label: "AVL", prob: 0.6 }];

  it("accents the segment the card's pick names, not the first one", () => {
    // The card states its pick in words on the line above, but the bar is the
    // same component as the panel's, and order-order emphasis was the defect
    // there. It would have been easy to leave this call site passing no pick and
    // let the bar lose the emphasis it used to have — for the right reason, and
    // with nothing put in its place.
    const { container } = render(<MatchCard {...base} pick={{ label: "AVL", prob: 0.6 }} bar={bar} />);
    const fills = [...container.querySelectorAll<HTMLElement>("[data-testid='pbar-fill']")].map((f) => f.style.backgroundColor);
    expect(fills).toEqual(["var(--color-pr-text-dim)", "var(--color-pr-accent)"]);
  });

  it("accents nothing on a card with no pick to point at", () => {
    const { container } = render(<MatchCard {...base} status="nopick" bar={bar} />);
    const fills = [...container.querySelectorAll<HTMLElement>("[data-testid='pbar-fill']")].map((f) => f.style.backgroundColor);
    expect(fills).not.toContain("var(--color-pr-accent)");
  });

  it("fails closed when the card's pick label is not one of the bar's", () => {
    // "Aston Villa win" is the card's wording for the pick; the bar's segments
    // are the teams. A site that does not line the two up gets no emphasis
    // rather than an emphasis on whichever segment happens to be first.
    const { container } = render(<MatchCard {...base} pick={{ label: "Aston Villa win", prob: 0.6 }} bar={bar} />);
    const fills = [...container.querySelectorAll<HTMLElement>("[data-testid='pbar-fill']")].map((f) => f.style.backgroundColor);
    expect(fills).not.toContain("var(--color-pr-accent)");
  });
});

/** The card's bar is inside a `<button>`. A button in a button is invalid HTML,
 *  and a browser is entitled not to make the inner one interactive at all — so
 *  the card's bar used to contribute two focus stops that announced a control
 *  and did nothing, inside the one control on the card that does something. */
describe("the card's bar inside the card's own button", () => {
  const bar = [{ label: "TOT", prob: 0.4 }, { label: "AVL", prob: 0.6 }];

  it("holds no control of its own, so the card's surface is the only affordance", () => {
    const { container } = render(<MatchCard {...base} pick={{ label: "AVL", prob: 0.6 }} bar={bar} />);
    const card = container.querySelector("button")!;
    // Every control on the card is the card. A descendant control is what React
    // warns about and what this assertion is here to keep out.
    expect(card.querySelectorAll("button, a[href], input, select, textarea, [tabindex]")).toHaveLength(0);
  });

  it("keeps the figures, in the labels and in the bar's name", () => {
    // Nothing is lost by not being focusable. The label text is the same
    // `{label} {prob}` the buttons carried, and the bar's `role="img"` name
    // still carries them plus which segment is the pick.
    const { container } = render(<MatchCard {...base} pick={{ label: "AVL", prob: 0.6 }} bar={bar} />);
    expect([...container.querySelectorAll("[data-seg]")].map((n) => n.textContent)).toEqual(["TOT 40%", "AVL 60%"]);
    expect(screen.getByRole("img", { name: "TOT 40%, AVL 60%, the pick is AVL" })).toBeInTheDocument();
  });

  it("leaves Enter and Space reaching the card rather than a dead inner control", async () => {
    const onOpen = vi.fn();
    const { container } = render(<MatchCard {...base} pick={{ label: "AVL", prob: 0.6 }} bar={bar} onOpen={onOpen} />);
    const card = container.querySelector("button")!;
    card.focus();
    expect(card).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    await userEvent.keyboard(" ");
    expect(onOpen).toHaveBeenCalledTimes(2);
    // The card is the only tab stop on it, so a keyboard reader never lands on a
    // segment label that has nothing to do.
    expect(container.querySelectorAll("[tabindex]")).toHaveLength(0);
  });

  it("cannot report a segment focus, so it has no highlight that a reader could get stuck in", () => {
    // §13c's highlight is a panel affordance and it is not reachable from here:
    // the card passes no `highlightKey`, so `dim` is a no-op and nothing on the
    // card can ever be highlighted — which is why the card needs no Escape, and
    // why a reader on a card has no highlight to clear. A card cannot offer the
    // panel's Escape either: the state that Escape clears does not exist in it.
    const { container } = render(<MatchCard {...base} pick={{ label: "AVL", prob: 0.6 }} bar={bar} />);
    expect([...container.querySelectorAll("[data-highlighted]")].map((n) => n.getAttribute("data-highlighted"))).toEqual(
      ["false", "false"],
    );
    expect(container.querySelector("[data-highlighted='true']")).toBeNull();
  });
});

describe("MatchCard review fixes", () => {
  it("reads the date and sport meta to screen readers too", () => {
    render(<MatchCard {...base} status="next" when="Sun 4 Oct · 12:00 PM CDT" meta="BAL −2.5 · Total 46.5" pick={{ label: "Ravens", prob: 0.62 }} />);
    expect(screen.getByRole("button")).toHaveAccessibleName(/Sun 4 Oct · 12:00 PM CDT.*BAL −2\.5 · Total 46\.5/);
  });
  it("always says 'No pick yet' when there is no pick, whatever the status", () => {
    render(<MatchCard {...base} status="next" />);
    expect(screen.getByText("No pick yet")).toBeInTheDocument();
  });
});

describe("MatchCard without a status", () => {
  it("shows no status words for an upcoming game that is not next, but still says when there is no pick", () => {
    render(<MatchCard left={base.left} right={base.right} centre="15:00" onOpen={() => {}} />);
    expect(screen.queryByText(/Next up|Live|Called it|Missed|Made after kickoff/)).toBeNull();
    expect(screen.getByText("No pick yet")).toBeInTheDocument();
  });
});

describe("MatchCard pick placeholder", () => {
  it("can say the pick is still loading instead of 'No pick yet'", () => {
    render(<MatchCard left={base.left} right={base.right} centre="15:00" pickPlaceholder="Loading pick…" onOpen={() => {}} />);
    expect(screen.getByText("Loading pick…")).toBeInTheDocument();
    expect(screen.queryByText("No pick yet")).toBeNull();
  });
});

describe("long team names", () => {
  it("wrap instead of being cut off", () => {
    render(<MatchCard left={{ code: "SM", name: "Southern Miss" }} right={{ code: "JS", name: "Jacksonville State" }} centre="12:00 AM" onOpen={() => {}} />);
    expect(screen.getByText("Jacksonville State").className).not.toMatch(/truncate/);
  });
});

describe("MatchCard in basketball", () => {
  it("says tip-off instead of kickoff when told the sport's moment", () => {
    render(<MatchCard {...base} status="rebuilt" moment="tip-off" pick={{ label: "AVL", prob: 0.6 }} />);
    expect(screen.getByText("Made after tip-off")).toBeInTheDocument();
    expect(screen.queryByText(/kickoff/i)).not.toBeInTheDocument();
  });

  it("has a compact form for busy nights, with the same content", () => {
    render(<MatchCard {...base} compact pick={{ label: "AVL", prob: 0.6 }} />);
    const card = screen.getByRole("button");
    expect(card).toHaveAttribute("data-compact", "true");
    expect(card).toHaveTextContent("Pick: AVL · 60%");
  });
});

describe("MatchCard top row", () => {
  it("keeps the status badge on one line, and lets it drop below the date on a narrow card", () => {
    render(<MatchCard {...base} status="rebuilt" moment="tip-off" when="Mon 2 Mar · Final" compact />);
    const badge = screen.getByText("Made after tip-off");
    expect(badge.className).toMatch(/whitespace-nowrap/);
    expect(badge.parentElement!.className).toMatch(/flex-wrap/);
  });
});
