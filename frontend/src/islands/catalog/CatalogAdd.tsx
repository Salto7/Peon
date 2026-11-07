import { useEffect, useState } from "react";
import { ExternalLink, FilePlus2, Hammer } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import InstallForm from "./InstallForm";
import MarketplacePanel from "./MarketplacePanel";

type Source = { value: "npx" | "git" | "disk"; label: string; placeholder?: string };
type MarketSource = {
  id: string;
  name: string;
  source_type: "github" | "zip";
  url: string;
  ref: string;
  subpath: string;
  status: "pending" | "scanning" | "scanned" | "error";
  error: string;
  discovered: Record<string, unknown>;
  last_scanned_at: string;
};

type Props = {
  skillInstallUrl: string;
  skillUploadUrl: string;
  skillSources: Source[];
  mcpInstallUrl: string;
  mcpUploadUrl: string;
  mcpSources: Source[];
  marketplaceListUrl: string;
  marketplaceUrls: { delete: string; scan: string; import: string };
  proposalUrls: { list: string; decide: string };
  marketplaceSources: MarketSource[];
  toolsmithUrl: string;
};

const CATALOG_ADD_EVENT = "peon:catalog-add";

type AddKind = "skill" | "mcp" | "tool" | "marketplace";

/** Centered Add-item dialog (install / marketplace / tool choice). Opened by ``catalog-shell.js``. */
export default function CatalogAdd({
  skillInstallUrl,
  skillUploadUrl,
  skillSources,
  mcpInstallUrl,
  mcpUploadUrl,
  mcpSources,
  marketplaceListUrl,
  marketplaceUrls,
  proposalUrls,
  marketplaceSources,
  toolsmithUrl,
}: Props) {
  const [kind, setKind] = useState<AddKind | null>(null);

  useEffect(() => {
    const onAdd = (ev: Event) => {
      const detail = (ev as CustomEvent<{ kind?: string }>).detail;
      const k = detail?.kind;
      if (k === "skill" || k === "mcp" || k === "tool" || k === "marketplace") setKind(k);
    };
    window.addEventListener(CATALOG_ADD_EVENT, onAdd);
    return () => window.removeEventListener(CATALOG_ADD_EVENT, onAdd);
  }, []);

  const openManualTool = () => {
    setKind(null);
    const fake = document.createElement("button");
    fake.type = "button";
    fake.setAttribute("data-catalog-edit", "tool");
    fake.setAttribute("data-id", "__new__");
    fake.hidden = true;
    document.body.appendChild(fake);
    fake.click();
    fake.remove();
  };

  const title =
    kind === "skill"
      ? "Install a skill"
      : kind === "mcp"
        ? "Install an MCP server"
        : kind === "marketplace"
          ? "Scan marketplace source"
          : kind === "tool"
            ? "Add a tool"
            : "";

  const description =
    kind === "skill"
      ? "From a git repository, the openskills marketplace, or a local folder."
      : kind === "mcp"
        ? "npx creates a package runner. Disk packs land under mcps/."
        : kind === "marketplace"
          ? "Scan a GitHub repo or zip — nothing imports until you approve each item."
          : kind === "tool"
            ? "Write a YAML definition by hand, or author one in Toolsmith."
            : "";

  return (
    <Dialog open={kind != null} onOpenChange={(o) => !o && setKind(null)}>
      <DialogContent
        showCloseButton
        className="flex max-h-[min(90dvh,40rem)] w-[calc(100%-2rem)] max-w-lg flex-col gap-0 overflow-hidden p-0 sm:max-w-xl"
      >
        <DialogHeader className="shrink-0 space-y-1.5 border-b px-5 py-4 pr-12 text-left">
          <DialogTitle className="text-base">{title}</DialogTitle>
          <DialogDescription className="text-pretty break-words">{description}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-5 py-4">
          {kind === "skill" && (
            <InstallForm
              kind="skill"
              installUrl={skillInstallUrl}
              uploadUrl={skillUploadUrl}
              sources={skillSources}
              submitLabel="Install skill"
            />
          )}
          {kind === "mcp" && (
            <InstallForm
              kind="mcp"
              installUrl={mcpInstallUrl}
              uploadUrl={mcpUploadUrl}
              sources={mcpSources}
              submitLabel="Install server"
            />
          )}
          {kind === "marketplace" && (
            <MarketplacePanel
              listUrl={marketplaceListUrl}
              urls={marketplaceUrls}
              proposalUrls={proposalUrls}
              sources={marketplaceSources}
              mode="add"
            />
          )}
          {kind === "tool" && (
            <div className="grid min-w-0 gap-3">
              <Button type="button" variant="secondary" className="h-auto flex-col items-start gap-1 p-4" onClick={openManualTool}>
                <span className="flex items-center gap-2 font-medium">
                  <FilePlus2 className="size-4" /> Manual YAML
                </span>
                <span className="text-left text-xs font-normal text-pretty text-muted-foreground">
                  Open the catalog editor and write a tool definition yourself.
                </span>
              </Button>
              <Button type="button" variant="secondary" className="h-auto flex-col items-start gap-1 p-4" asChild>
                <a href={toolsmithUrl}>
                  <span className="flex items-center gap-2 font-medium">
                    <Hammer className="size-4" /> Toolsmith
                    <ExternalLink className="size-3 opacity-60" />
                  </span>
                  <span className="text-left text-xs font-normal text-pretty text-muted-foreground">
                    Author and test a tool in the lab, then save it to the catalog.
                  </span>
                </a>
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
