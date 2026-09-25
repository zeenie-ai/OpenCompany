/**
 * The shared catalog page: Discover shows eight until "Show all"; the
 * category filter reveals its chips and clears when closed; Yours shows the
 * page's own list with a switch only when the page can toggle and remove
 * only when it can remove; an empty Yours points to Discover; a card that
 * turns added while on screen calls back once.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider } from '@/contexts/ThemeContext';
import type { CatalogItem } from '../settings/catalog';
import { CatalogLayout, type CatalogLayoutProps } from '../settings/CatalogLayout';

function item(id: string, patch: Partial<CatalogItem> = {}): CatalogItem {
  return { id, name: `Item ${id}`, tile: {}, state: 'available', ...patch };
}

const EMPTY = { title: 'Nothing yours yet', detail: 'Add one from Discover.' };

function renderLayout(props: Partial<CatalogLayoutProps> = {}) {
  const base: CatalogLayoutProps = {
    title: 'Things',
    searchPlaceholder: 'Search things',
    yours: { items: [], sectionTitle: 'Your things', empty: EMPTY },
    discover: { items: [], sectionTitle: 'Top things' },
    verbs: { add: 'Add', remove: 'Remove' },
    onAdd: vi.fn(),
    ...props,
  };
  const view = (next: Partial<CatalogLayoutProps> = {}) => (
    <ThemeProvider>
      <CatalogLayout {...base} {...next} />
    </ThemeProvider>
  );
  const utils = render(view());
  return { ...utils, rerenderWith: (next: Partial<CatalogLayoutProps>) => utils.rerender(view(next)) };
}

const cards = () => document.querySelectorAll('[data-catalog-item]');
const showYours = () => fireEvent.click(screen.getByRole('radio', { name: /Yours/ }));

describe('CatalogLayout', () => {
  it('shows eight in Discover until Show all', () => {
    renderLayout({ discover: { items: Array.from({ length: 10 }, (_, i) => item(String(i))), sectionTitle: 'Top things' } });
    expect(cards()).toHaveLength(8);
    fireEvent.click(screen.getByRole('button', { name: /Show all/ }));
    expect(cards()).toHaveLength(10);
    expect(screen.getByRole('button', { name: /Show less/ })).toBeInTheDocument();
  });

  it('reveals the category chips, and closing the filter clears the category', () => {
    renderLayout({
      categories: [
        { key: 'a', label: 'Alpha' },
        { key: 'b', label: 'Beta' },
      ],
      discover: { items: [item('1', { category: 'a' }), item('2', { category: 'b' })], sectionTitle: 'Top things' },
    });
    expect(screen.queryByRole('radio', { name: 'Beta' })).not.toBeInTheDocument();
    const filter = screen.getByRole('button', { name: 'Filter by category' });
    fireEvent.click(filter);
    fireEvent.click(screen.getByRole('radio', { name: 'Beta' }));
    expect(cards()).toHaveLength(1);
    fireEvent.click(filter);
    expect(screen.queryByRole('radio', { name: 'Beta' })).not.toBeInTheDocument();
    expect(cards()).toHaveLength(2);
  });

  it('has no filter without categories, and starts filtered when opened on one', () => {
    const { unmount } = renderLayout();
    expect(screen.queryByRole('button', { name: 'Filter by category' })).not.toBeInTheDocument();
    unmount();
    renderLayout({
      categories: [{ key: 'a', label: 'Alpha' }],
      initialCategory: 'a',
      discover: { items: [item('1', { category: 'a' }), item('2')], sectionTitle: 'Top things' },
    });
    expect(screen.getByRole('radio', { name: 'Alpha' })).toHaveAttribute('aria-checked', 'true');
    expect(cards()).toHaveLength(1);
  });

  it('shows a switch in Yours only when the page can toggle, and remove only when it can remove', () => {
    const onToggle = vi.fn();
    const added = item('1', { state: 'added', enabled: true });
    const lists = { yours: { items: [added], sectionTitle: 'Your things', empty: EMPTY }, discover: { items: [added], sectionTitle: 'Top things' } };
    const { rerenderWith } = renderLayout({ ...lists, onToggle });
    expect(screen.getByRole('img', { name: 'Item 1 added' })).toBeInTheDocument();
    showYours();
    fireEvent.click(screen.getByRole('switch', { name: 'Item 1' }));
    expect(onToggle).toHaveBeenCalledWith(added, false);
    expect(screen.queryByRole('button', { name: 'Remove Item 1' })).not.toBeInTheDocument();

    const onRemove = vi.fn();
    rerenderWith({ ...lists, onRemove, onToggle: undefined });
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Item 1' }));
    expect(onRemove).toHaveBeenCalledWith(added);
  });

  it('points an empty Yours to Discover', () => {
    renderLayout({ discover: { items: [item('1')], sectionTitle: 'Top things' } });
    showYours();
    expect(screen.getByText('Nothing yours yet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }));
    expect(screen.getByRole('radio', { name: 'Discover' })).toHaveAttribute('aria-checked', 'true');
    expect(cards()).toHaveLength(1);
  });

  it('calls back once when an item on screen turns added, never on first render', () => {
    const onItemAdded = vi.fn();
    const { rerenderWith } = renderLayout({
      discover: { items: [item('1', { state: 'added' }), item('2')], sectionTitle: 'Top things' },
      onItemAdded,
    });
    expect(onItemAdded).not.toHaveBeenCalled();
    rerenderWith({ discover: { items: [item('1', { state: 'added' }), item('2', { state: 'added' })], sectionTitle: 'Top things' }, onItemAdded });
    expect(onItemAdded).toHaveBeenCalledTimes(1);
    expect(onItemAdded).toHaveBeenCalledWith(expect.objectContaining({ id: '2' }));
  });

  it('opens and closes the primary action form', () => {
    renderLayout({ primaryAction: { label: 'Create', form: (close) => <button onClick={close}>Done here</button> } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    fireEvent.click(screen.getByRole('button', { name: 'Done here' }));
    expect(screen.queryByRole('button', { name: 'Done here' })).not.toBeInTheDocument();
  });
});
