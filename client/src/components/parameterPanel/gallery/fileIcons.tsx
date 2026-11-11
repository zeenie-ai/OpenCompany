import type { LucideIcon } from 'lucide-react';
import {
  File as FileGlyphIcon,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder,
} from 'lucide-react';

import type { WorkspaceEntry } from '@/types/workspaceFiles';

/**
 * Presentation only.
 *
 * Which glyph, nothing more: nothing here decides what a file *is*. The row
 * already carries `preview` (the server's inline-rendering verdict) and
 * `mime_type`; this module just picks a shape for them. Anything that would
 * amount to re-classifying a file belongs in
 * `server/nodes/filesystem/gallery/_service.py`.
 *
 * The shape is the whole signal. Every glyph is drawn in the muted text
 * colour, because colour marks a state or a role and a file's kind is
 * neither (theme_system.md, "Colour marks state or role").
 */

const extensionOf = (name: string): string => {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
};

const EXTENSION_GLYPH: Record<string, LucideIcon> = {
  json: FileJson, jsonl: FileJson,
  csv: FileSpreadsheet, tsv: FileSpreadsheet, xlsx: FileSpreadsheet, xls: FileSpreadsheet,
  zip: FileArchive, gz: FileArchive, tar: FileArchive, tgz: FileArchive,
  rar: FileArchive, '7z': FileArchive,
  py: FileCode, js: FileCode, jsx: FileCode, ts: FileCode, tsx: FileCode,
  sh: FileCode, ps1: FileCode, rb: FileCode, go: FileCode, rs: FileCode,
  java: FileCode, c: FileCode, cpp: FileCode, h: FileCode, sql: FileCode,
  html: FileCode, css: FileCode, xml: FileCode, yaml: FileCode, yml: FileCode,
  toml: FileCode,
  md: FileText, txt: FileText, log: FileText, pdf: FileText, rtf: FileText,
};

/** The glyph for a row. Directories always win over any extension match. */
export const glyphFor = (entry: WorkspaceEntry): LucideIcon => {
  if (entry.is_dir) return Folder;

  switch (entry.preview) {
    case 'image': return FileImage;
    case 'audio': return FileAudio;
    case 'video': return FileVideo;
    default: break;
  }

  // preview === 'none' still covers media the route refuses to serve
  // inline — an SVG is a picture even though it will never render here.
  const mime = entry.mime_type || '';
  if (mime.startsWith('image/')) return FileImage;
  if (mime.startsWith('audio/')) return FileAudio;
  if (mime.startsWith('video/')) return FileVideo;
  if (mime.startsWith('text/')) return FileText;

  return EXTENSION_GLYPH[extensionOf(entry.name)] ?? FileGlyphIcon;
};

/** Human byte size. Directories report nothing rather than a misleading 0 B. */
export const formatBytes = (bytes: number, isDir = false): string => {
  if (isDir) return '—';
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${exponent === 0 ? value : value.toFixed(value < 10 ? 1 : 0)} ${units[exponent]}`;
};

/**
 * Locale-aware timestamp. `Intl` with an undefined locale follows the host,
 * so this reads correctly on every platform without a format string.
 */
export const formatModified = (value: string | null): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
};
