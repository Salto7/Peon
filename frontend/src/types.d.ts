declare module "cytoscape-fcose" {
  import type { Ext } from "cytoscape";
  const ext: Ext;
  export default ext;
}

declare module "cytoscape-edgehandles" {
  import type { Ext, NodeSingular } from "cytoscape";

  export interface EdgeHandlesOptions {
    canConnect?: (source: NodeSingular, target: NodeSingular) => boolean;
    edgeParams?: (source: NodeSingular, target: NodeSingular) => Record<string, unknown>;
    hoverDelay?: number;
    snap?: boolean;
    snapThreshold?: number;
    snapFrequency?: number;
    noEdgeEventsInDraw?: boolean;
    disableBrowserGestures?: boolean;
  }

  export interface EdgeHandlesInstance {
    enable(): void;
    disable(): void;
    enableDrawMode(): void;
    disableDrawMode(): void;
    destroy(): void;
  }

  const ext: Ext;
  export default ext;

  // Augment cytoscape.Core with the `.edgehandles()` factory the plugin adds.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  type _Augment = (opts?: EdgeHandlesOptions) => EdgeHandlesInstance;
}

declare namespace cytoscape {
  interface Core {
    edgehandles: (
      opts?: import("cytoscape-edgehandles").EdgeHandlesOptions,
    ) => import("cytoscape-edgehandles").EdgeHandlesInstance;
  }
}
