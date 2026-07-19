import { useCallback } from 'react';
import { useAppStore } from '../store/useAppStore';
import { useWorkflowsQuery, type SavedWorkflow } from './useWorkflowsQuery';

export const useWorkflowManagement = () => {
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);
  const hasUnsavedChanges = useAppStore((s) => s.hasUnsavedChanges);
  const updateWorkflow = useAppStore((s) => s.updateWorkflow);
  const saveWorkflow = useAppStore((s) => s.saveWorkflow);
  const loadWorkflow = useAppStore((s) => s.loadWorkflow);
  const createNewWorkflow = useAppStore((s) => s.createNewWorkflow);

  const { data: savedWorkflows = [] } = useWorkflowsQuery();

  const handleWorkflowNameChange = useCallback((name: string) => {
    updateWorkflow({ name });
  }, [updateWorkflow]);

  const handleSave = useCallback(() => {
    saveWorkflow();
  }, [saveWorkflow]);

  const handleNew = useCallback(async () => {
    await createNewWorkflow();
  }, [createNewWorkflow]);

  const handleOpen = useCallback(() => {
    // Workflow selection is handled by sidebar
  }, []);

  const handleSelectWorkflow = useCallback((workflow: SavedWorkflow) => {
    loadWorkflow(workflow.id);
  }, [loadWorkflow]);


  return {
    currentWorkflow,
    hasUnsavedChanges,
    savedWorkflows,
    handleWorkflowNameChange,
    handleSave,
    handleNew,
    handleOpen,
    handleSelectWorkflow,
  };
};
