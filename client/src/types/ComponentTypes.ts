import { INodeTypeDescription } from './INodeProperties';

export interface ComponentPaletteState {
  collapsedSections: Record<string, boolean>;
  searchQuery: string;
}

export interface ComponentPaletteActions {
  onSearchChange: (query: string) => void;
  onToggleSection: (sectionId: string) => void;
  onDragStart: (event: React.DragEvent, definition: INodeTypeDescription) => void;
}

export interface ComponentPaletteProps extends ComponentPaletteState, ComponentPaletteActions {
  proMode?: boolean;  // false = simple mode (only AI categories), true = pro mode (all categories)
  // Identity of the cached spec-type set (cachedNodeSpecTypesKey).
  // Changes when Dashboard's prefetchAllNodeSpecs lands a different
  // catalogue so the palette's useMemo recomputes against the now-warm
  // spec cache — including types added by a backend revision bust.
  specsKey?: string;
}

export interface WorkflowHandlers {
  handleWorkflowNameChange: (name: string) => void;
  handleSave: () => void;
  handleNew: () => void;
  handleOpen: () => void;
  handleSelectWorkflow: (workflow: any) => void;
  handleDeleteWorkflow: (id: string) => void;
  handleDuplicateWorkflow: (workflow: any) => void;
}

export interface DragDropHandlers {
  onDragOver: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
  handleComponentDragStart: (event: React.DragEvent, definition: any) => void;
}

export interface ReactFlowHandlers {
  onConnect: (params: any) => void;
  onNodesDelete: (deleted: any[]) => void;
  onEdgesDelete: (deleted: any[]) => void;
}