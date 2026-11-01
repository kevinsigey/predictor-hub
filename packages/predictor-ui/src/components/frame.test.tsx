import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AppFrame, type SiteLink } from "./AppFrame";
import { RoundNavigator } from "./RoundNavigator";

const sites: SiteLink[] = [
  { sport: "hub", label: "Home", href: "https://example" },
  { sport: "pl", label: "PL", href: "https://pl.example" },
  { sport: "f1", label: "F1", href: "https://f1.example" },
  { sport: "nfl", label: "NFL", href: "https://sports.example/?sport=nfl" },
  { sport: "cfb", label: "CFB", href: "https://sports.example/?sport=cfb" },
  { sport: "nba", label: "NBA", href: "https://nba.example" },
];
const tabs = [{ id: "games", label: "Games" }, { id: "hub", label: "Data Hub" }, { id: "model", label: "Model" }];

describe("AppFrame", () => {
  it("shows the family wordmark and the sport, and sets data-sport for the accent", () => {
    const { container } = render(<AppFrame sport="nba" sportName="NBA" sites={sites} tabs={tabs} activeTab="games" onTab={() => {}}>body</AppFrame>);
    expect(screen.getByText("Predictor")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("NBA Predictor");
    expect(container.firstElementChild).toHaveAttribute("data-sport", "nba");
  });
  it("links to every sport and marks the current one", () => {
    render(<AppFrame sport="nfl" sportName="NFL" sites={sites} tabs={tabs} activeTab="games" onTab={() => {}}>body</AppFrame>);
    const nav = screen.getByRole("navigation", { name: "Sports" });
    expect(nav.querySelectorAll("a")).toHaveLength(6);
    expect(screen.getByRole("link", { name: "NFL" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "NBA" })).not.toHaveAttribute("aria-current");
  });
  it("leads the switcher with a Home entry to the hub, behind the current sport", () => {
    const { container } = render(<AppFrame sport="pl" sportName="PL" sites={sites} tabs={tabs} activeTab="games" onTab={() => {}}>body</AppFrame>);
    const nav = screen.getByRole("navigation", { name: "Sports" });
    const first = nav.querySelectorAll("a")[0];
    expect(first).toHaveTextContent("Home");
    expect(first).toHaveAttribute("href", "https://example");
    expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "PL" })).toHaveAttribute("aria-current", "page");
    // The wordmark links home too but does not claim to be the current page:
    // the sport entry is the one current location.
    expect(screen.getByRole("link", { name: "Predictor" })).not.toHaveAttribute("aria-current");
    expect(container.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });
  it("marks Home current on a hub frame, not the wordmark", () => {
    const { container } = render(<AppFrame sport="hub" sportName="Sports" sites={sites} tabs={tabs} activeTab="games" onTab={() => {}}>body</AppFrame>);
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "https://example");
    expect(screen.getByRole("link", { name: "Predictor" })).not.toHaveAttribute("aria-current");
    expect(container.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });
  it("switches pages through a real tablist", () => {
    const onTab = vi.fn();
    render(<AppFrame sport="pl" sportName="PL" sites={sites} tabs={tabs} activeTab="games" onTab={onTab}>body</AppFrame>);
    const pages = screen.getByRole("tablist", { name: "Pages" });
    expect(pages.querySelectorAll("[role='tab']")).toHaveLength(tabs.length);
    expect(screen.getByRole("tab", { name: "Games" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Data Hub" }));
    expect(onTab).toHaveBeenCalledWith("hub");
  });
});

describe("RoundNavigator", () => {
  it("steps between rounds and disables the ends", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(<RoundNavigator label="Gameweek 5" canPrev canNext={false} onPrev={onPrev} onNext={onNext} />);
    expect(screen.getByRole("heading", { name: "Gameweek 5" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Previous week" }));
    expect(onPrev).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Next week" })).toBeDisabled();
  });
  it("offers a jump back to the current round", () => {
    const jump = vi.fn();
    render(<RoundNavigator label="Week 2" canPrev canNext onPrev={() => {}} onNext={() => {}} onJumpToCurrent={jump} />);
    fireEvent.click(screen.getByRole("button", { name: "Jump to current week" }));
    expect(jump).toHaveBeenCalledOnce();
  });
  it("shows the pre-kickoff record, and says so honestly when every pick was rebuilt", () => {
    const { rerender } = render(<RoundNavigator label="Week 4" unit="week" canPrev canNext onPrev={() => {}} onNext={() => {}} record={{ hits: 4, settled: 7, rebuilt: 1 }} />);
    expect(screen.getByText("4/7 picks made before kickoff correct")).toBeInTheDocument();
    rerender(<RoundNavigator label="Week 4" unit="week" canPrev canNext onPrev={() => {}} onNext={() => {}} record={{ hits: 0, settled: 0, rebuilt: 3 }} />);
    expect(screen.getByText("No picks made before kickoff this week")).toBeInTheDocument();
    rerender(<RoundNavigator label="Week 4" unit="week" canPrev canNext onPrev={() => {}} onNext={() => {}} record={{ hits: 0, settled: 0, rebuilt: 0 }} />);
    expect(screen.queryByText(/picks/)).not.toBeInTheDocument();
  });
});

describe("RoundNavigator in basketball", () => {
  it("words the record around tip-off", () => {
    const noop = () => {};
    const { rerender } = render(
      <RoundNavigator label="Oct 19 – 25" moment="tip-off" canPrev canNext onPrev={noop} onNext={noop} record={{ hits: 3, settled: 5, rebuilt: 1 }} />,
    );
    expect(screen.getByText("3/5 picks made before tip-off correct")).toBeInTheDocument();
    rerender(<RoundNavigator label="Oct 19 – 25" moment="tip-off" canPrev canNext onPrev={noop} onNext={noop} record={{ hits: 0, settled: 0, rebuilt: 4 }} />);
    expect(screen.getByText("No picks made before tip-off this week")).toBeInTheDocument();
  });
});
