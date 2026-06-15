import * as React from 'react';

/**
 * SettingsSection — elevated settings card with toned icon tile + title.
 * Tones map to node role colors (agent purple, model cyan, workflow
 * orange, tool green).
 */
export interface SettingsSectionProps {
  title: React.ReactNode;
  /** 16px icon node for the 32px tile */
  icon?: React.ReactNode;
  /** Node-role tone or any CSS color. Default 'agent' */
  tone?: 'agent' | 'model' | 'workflow' | 'tool' | 'trigger' | string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

export function SettingsSection(props: SettingsSectionProps): JSX.Element;

/** SettingsRow — label + description left, control right. */
export interface SettingsRowProps {
  label: React.ReactNode;
  description?: React.ReactNode;
  /** The control (Switch, Input, Slider, Button) */
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

export function SettingsRow(props: SettingsRowProps): JSX.Element;
