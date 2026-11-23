/**
 * useWorkflowValidation — debounced workflow validation against the backend.
 *
 * Calls the `validate_workflow` WebSocket handler (server/services/workflow_validator.py)
 * whenever `nodes` or `edges` change, with a 500ms trailing-edge debounce.
 * Returns the latest `{errors, warnings}` report for editor live-lint.
 *
 * The same WS handler is reused at execute-time gating
 * (`handle_execute_workflow` with `force=false`) and at import-time
 * dry-run, so the editor preview matches what the runtime gate sees.
 */

import { useEffect, useRef, useState } from 'react';
import type { Node, Edge } from 'reactflow';
import { useWebSocket } from '../contexts/WebSocketContext';
import { useAppStore } from '../store/useAppStore';

export interface ValidationIssue {
  code:
    | 'CYCLE'
    | 'DANGLING_EDGE'
    | 'UNKNOWN_NODE_TYPE'
    | 'INVALID_PARAM'
    | 'MISSING_CREDENTIAL';
  node_id: string | null;
  message: string;
  node_type?: string;
  provider_id?: string;
  remediation?: 'add_key' | 'reconnect';
  path?: Array<string | number>;
  nodes?: string[];
}

export interface ValidationReport {
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
}

const EMPTY_REPORT: ValidationReport = { errors: [], warnings: [] };

const DEBOUNCE_MS = 500;

export interface UseWorkflowValidationResult {
  report: ValidationReport;
  isValidating: boolean;
  /** Manually trigger a validate (skips the debounce). Returns the fresh report. */
  validate: () => Promise<ValidationReport>;
}

/**
 * Validate the workflow on every change to `nodes` / `edges`.
 *
 * @param nodes ReactFlow nodes
 * @param edges ReactFlow edges
 * @param parametersById Optional map of node_id -> parameters for INVALID_PARAM
 *   detection. The frontend can pass the parameter-panel's in-flight state so
 *   the lint reflects unsaved edits; if omitted, the backend falls back to
 *   `node.data.parameters` (rarely populated since parameters live in the DB).
 * @param enabled  Set false to suspend validation (e.g. during workflow load).
 */
export function useWorkflowValidation(
  nodes: Node[],
  edges: Edge[],
  parametersById?: Record<string, Record<string, any>>,
  enabled: boolean = true,
): UseWorkflowValidationResult {
  const { sendRequest, isReady } = useWebSocket();
  const [report, setReport] = useState<ValidationReport>(EMPTY_REPORT);
  const [isValidating, setIsValidating] = useState(false);

  // Latest-write-wins guard so a slow in-flight request doesn't overwrite a
  // newer report with a stale one.
  const requestSeqRef = useRef(0);

  const runValidate = useRef(async (): Promise<ValidationReport> => EMPTY_REPORT);
  runValidate.current = async () => {
    if (!isReady || !enabled) return EMPTY_REPORT;
    const seq = ++requestSeqRef.current;
    setIsValidating(true);
    try {
      const result = await sendRequest<{ success: boolean; report?: ValidationReport }>(
        'validate_workflow',
        {
          workflow_id: useAppStore.getState().currentWorkflow?.id,
          nodes: nodes.map(n => ({
            id: n.id,
            type: n.type ?? '',
            data: n.data ?? {},
          })),
          edges: edges.map(e => ({
            id: e.id,
            source: e.source,
            target: e.target,
            sourceHandle: e.sourceHandle ?? undefined,
            targetHandle: e.targetHandle ?? undefined,
          })),
          parameters_by_id: parametersById,
        },
      );
      const fresh = result?.report ?? EMPTY_REPORT;
      // Drop the response if a newer request has already started.
      if (seq === requestSeqRef.current) {
        setReport(fresh);
      }
      return fresh;
    } catch (err) {
      // Surface as an error issue so the editor doesn't silently mask the
      // backend round-trip failing.
      const errorReport: ValidationReport = {
        errors: [
          {
            code: 'UNKNOWN_NODE_TYPE',
            node_id: null,
            message: `Validation request failed: ${err instanceof Error ? err.message : String(err)}`,
          },
        ],
        warnings: [],
      };
      if (seq === requestSeqRef.current) {
        setReport(errorReport);
      }
      return errorReport;
    } finally {
      if (seq === requestSeqRef.current) {
        setIsValidating(false);
      }
    }
  };

  useEffect(() => {
    if (!enabled || !isReady) return;
    const timer = setTimeout(() => {
      void runValidate.current();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
     
  }, [nodes, edges, parametersById, enabled, isReady]);

  return {
    report,
    isValidating,
    validate: () => runValidate.current(),
  };
}
