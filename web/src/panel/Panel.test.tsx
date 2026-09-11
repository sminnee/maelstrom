import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import { renderApp, VIEWPORTS } from '../test/renderApp';

const panelWidth = () => Number.parseFloat(screen.getByTestId('panel').style.width);

describe('the panel opens wide enough to read in', () => {
  it('takes half the window, so the canvas keeps the other half', async () => {
    await renderApp();
    // The wide viewport is 1440.
    expect(panelWidth()).toBe(VIEWPORTS.wide / 2);
  });

  it('never opens past the window it has to share', async () => {
    await renderApp();
    // The grip lives on the panel's left edge, so a panel as wide as the
    // window would put it off-screen with no way to drag it back.
    expect(panelWidth()).toBeLessThan(VIEWPORTS.wide);
  });
});
