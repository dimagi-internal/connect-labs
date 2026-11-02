/**
 * The Share control on a saved run's row on the workflow list: choose how long a public
 * link lasts, copy it, end it. The server decides who may (workflow/public_share_views.py).
 */
window.publicRunShare = function (url, csrfToken, startsShared) {
  return {
    open: false,
    shared: Boolean(startsShared),
    shares: [],
    duration: '7',
    until: '',
    confirmed: false,
    busy: false,
    error: '',
    copied: null,

    async show() {
      this.open = true;
      this.error = '';
      await this.load().catch((e) => {
        this.error = e.message;
      });
    },

    async load() {
      const response = await fetch(url, { credentials: 'same-origin' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Could not load links.');
      this.shares = body.shares;
      this.shared = this.shares.length > 0;
    },

    async post(address, payload) {
      const response = await fetch(address, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'That did not work.');
      return body;
    },

    async act(work) {
      this.busy = true;
      this.error = '';
      try {
        await work();
        await this.load();
      } catch (e) {
        this.error = e.message;
      } finally {
        this.busy = false;
      }
    },

    canCreate() {
      return (
        !this.busy &&
        this.confirmed &&
        (this.duration !== 'date' || Boolean(this.until))
      );
    },

    create() {
      return this.act(async () => {
        await this.post(url, {
          confirmed: true,
          ...(this.duration === 'date'
            ? { until: this.until }
            : { days: Number(this.duration) }),
        });
        this.confirmed = false;
      });
    },

    end(id) {
      return this.act(() =>
        this.post(`/labs/workflow/api/public-shares/${id}/end/`, {}),
      );
    },

    async copy(share) {
      try {
        await navigator.clipboard.writeText(share.url);
        this.copied = share.id;
      } catch (e) {
        this.error = 'Could not copy. Copy the link from here: ' + share.url;
      }
    },

    summary(share) {
      const views =
        share.view_count === 1 ? '1 view' : `${share.view_count} views`;
      const by = share.created_by ? ` · by ${share.created_by}` : '';
      return `Ends ${share.expires_at.slice(0, 10)} · ${views}${by}`;
    },
  };
};
