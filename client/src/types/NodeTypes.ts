export interface NodeParameter {
  name: string;
  displayName: string;
  type: 'string' | 'number' | 'boolean' | 'select' | 'slider' | 'percentage' | 'file' | 'text' | 'array' | 'options' | 'multiOptions' | 'collection' | 'fixedCollection' | 'color' | 'dateTime' | 'notice' | 'hidden' | 'resourceLocator' | 'code' | 'json';
  default?: any;
  placeholder?: string;
  description?: string;
  options?: Array<{ value: any; label?: string; name?: string }>;
  min?: number;
  max?: number;
  step?: number;
  required?: boolean;
}

export interface NodeOutput {
  name: string;
  displayName: string;
  type: 'string' | 'number' | 'boolean' | 'file' | 'array' | 'object' | string;
  description: string;
}


export interface NodeData {
  label?: string;
  disabled?: boolean; // Skip execution when true (n8n-style disable)
  customIcon?: string; // Custom icon: emoji, text, or image URL (http://, https://, data:, or /)
  [key: string]: any;
}

/**
 * CSSProperties extended to accept arbitrary CSS custom properties (e.g.
 * `--node-color`). React's CSSProperties type rejects custom-property keys
 * by default; this NodeStyle alias lets canvas-node components pass the
 * per-definition node accent through to per-theme decorative CSS via a
 * `--node-color` variable.
 */
import type { CSSProperties } from 'react';
export type NodeStyle = CSSProperties & Record<`--${string}`, string | number>;