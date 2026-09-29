// Full-page loading screen: one row per loading stage (with its own bar and detail text) and an
// overall bar that combines the stages by weight. Stages without a measurable size show a moving
// stripe instead of a fraction. The screen fades out once every stage has finished or been skipped.

export interface LoadingStageDefinition {
  id: string;
  label: string;
  /** Share of the overall bar this stage fills (any positive number; weights are normalised). */
  weight: number;
}

type StageState = "waiting" | "active" | "done" | "skipped" | "failed";

interface StageRow {
  definition: LoadingStageDefinition;
  state: StageState;
  fraction: number | null;
  element: HTMLLIElement;
  bar: HTMLDivElement;
  detail: HTMLSpanElement;
}

export function formatMegabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export class LoadingScreen {
  private readonly root: HTMLDivElement;
  private readonly overallBar: HTMLDivElement;
  private readonly overallLabel: HTMLSpanElement;
  private readonly stages = new Map<string, StageRow>();

  constructor(root: HTMLDivElement, stageDefinitions: LoadingStageDefinition[]) {
    this.root = root;
    this.overallBar = root.querySelector<HTMLDivElement>(".loading-overall .loading-bar-fill")!;
    this.overallLabel = root.querySelector<HTMLSpanElement>(".loading-overall-percent")!;
    const list = root.querySelector<HTMLOListElement>(".loading-stages")!;
    list.innerHTML = "";
    for (const definition of stageDefinitions) {
      const element = document.createElement("li");
      element.className = "loading-stage waiting";
      element.innerHTML = `<div class="loading-stage-head"><span class="loading-stage-icon"></span><span class="loading-stage-label"></span><span class="loading-stage-detail"></span></div><div class="loading-bar"><div class="loading-bar-fill"></div></div>`;
      element.querySelector<HTMLSpanElement>(".loading-stage-label")!.textContent = definition.label;
      list.appendChild(element);
      this.stages.set(definition.id, {
        definition,
        state: "waiting",
        fraction: 0,
        element,
        bar: element.querySelector<HTMLDivElement>(".loading-bar-fill")!,
        detail: element.querySelector<HTMLSpanElement>(".loading-stage-detail")!,
      });
    }
    this.root.hidden = false;
    this.render();
  }

  /** Mark a stage as running. `fraction` null means progress cannot be measured (moving stripe). */
  start(stageId: string, detail = "", fraction: number | null = null): void {
    this.update(stageId, { state: "active", fraction, detail });
  }

  progress(stageId: string, fraction: number | null, detail?: string): void {
    this.update(stageId, { state: "active", fraction: fraction === null ? null : Math.max(0, Math.min(1, fraction)), detail });
  }

  done(stageId: string, detail?: string): void {
    this.update(stageId, { state: "done", fraction: 1, detail });
  }

  skip(stageId: string, detail: string): void {
    this.update(stageId, { state: "skipped", fraction: 1, detail });
  }

  fail(stageId: string, detail: string): void {
    this.update(stageId, { state: "failed", fraction: 1, detail });
  }

  /** Fade the screen out and remove it from the page's hit testing. */
  finish(): void {
    this.overallBar.style.width = "100%";
    this.overallLabel.textContent = "100%";
    this.root.classList.add("finished");
    setTimeout(() => (this.root.hidden = true), 600);
  }

  private update(stageId: string, change: { state: StageState; fraction: number | null; detail?: string }): void {
    const stage = this.stages.get(stageId);
    if (!stage) return;
    stage.state = change.state;
    stage.fraction = change.fraction;
    if (change.detail !== undefined) stage.detail.textContent = change.detail;
    this.render();
  }

  private render(): void {
    let totalWeight = 0;
    let filledWeight = 0;
    for (const stage of this.stages.values()) {
      totalWeight += stage.definition.weight;
      const finished = stage.state === "done" || stage.state === "skipped" || stage.state === "failed";
      // An unmeasurable active stage counts as a third done, so the overall bar still moves.
      const fraction = finished ? 1 : stage.state === "active" ? (stage.fraction ?? 0.33) : 0;
      filledWeight += stage.definition.weight * fraction;
      stage.element.className = `loading-stage ${stage.state}${stage.state === "active" && stage.fraction === null ? " indeterminate" : ""}`;
      stage.bar.style.width = `${Math.round((stage.state === "active" && stage.fraction === null ? 1 : stage.state === "waiting" ? 0 : (stage.fraction ?? 0)) * 100)}%`;
    }
    const overall = totalWeight > 0 ? filledWeight / totalWeight : 0;
    this.overallBar.style.width = `${(overall * 100).toFixed(1)}%`;
    this.overallLabel.textContent = `${Math.floor(overall * 100)}%`;
  }
}
