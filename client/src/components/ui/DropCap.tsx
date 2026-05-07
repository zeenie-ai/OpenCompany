/**
 * Wraps content (typically a heading or first paragraph) with the
 * `v-display drop-cap` className so theme CSS rules like the
 * Renaissance `.v-display.drop-cap::first-letter` rule fire on the
 * first letter. Under themes that don't define a drop-cap rule it's a
 * no-op (just two extra class names on the wrapper).
 */
import React from 'react';

export interface DropCapProps {
  as?: 'h1' | 'h2' | 'h3' | 'p' | 'span' | 'div';
  className?: string;
  children: React.ReactNode;
}

export const DropCap: React.FC<DropCapProps> = ({
  as: Tag = 'span',
  className = '',
  children,
}) => {
  return (
    <Tag className={`v-display drop-cap ${className}`.trim()}>
      {children}
    </Tag>
  );
};

export default DropCap;
