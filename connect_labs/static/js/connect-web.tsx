/**
 * Connect Web (connect_labs/connect_web): the form player on the form page, and the "waiting for
 * Connect" poller on the opportunity page after a submission. One bundle for both.
 */
import React from 'react';
import { createRoot } from 'react-dom/client';

import { Player } from '../connect_web/Player';
import { mountProgress } from '../connect_web/progress';
import type { PlayerConfig } from '../connect_web/api';

function start() {
  const root = document.getElementById('cw-form-player-root');
  const configEl = document.getElementById('cw-player-config');
  if (root && configEl) {
    const config = JSON.parse(configEl.textContent || '{}') as PlayerConfig;
    createRoot(root).render(<Player config={config} />);
  }
  const banner = document.querySelector<HTMLElement>('[data-cw-progress]');
  if (banner) mountProgress(banner);
}

if (document.readyState === 'loading')
  document.addEventListener('DOMContentLoaded', start);
else start();
