/**
 * Report Builder Library
 *
 * Provides helper functions and React hooks for building reports.
 * This library is automatically loaded in the iframe and available
 * to user code via window.labsApi and window.hooks.
 */

(function () {
  'use strict';

  // Library functions available to user code but hidden from editor
  window.labsApi = {
    // Fetch FLW analysis data
    async fetchFLWData(config = {}) {
      const opportunityId = window.PARENT_OPPORTUNITY_ID;
      if (!opportunityId) {
        throw new Error('No opportunity_id found in URL');
      }

      const params = new URLSearchParams();
      params.set('opportunity_id', opportunityId);

      // Add any additional config params
      if (config.config) params.set('config', config.config);
      if (config.refresh) params.set('refresh', '1');
      if (config.useLabsRecordCache)
        params.set('use_labs_record_cache', 'true');

      const response = await fetch(
        '/labs/api/analysis/flw/?' + params.toString(),
      );

      if (!response.ok) {
        const errorData = await response
          .json()
          .catch(() => ({ error: 'Unknown error' }));
        throw new Error(errorData.error || `HTTP ${response.status}`);
      }

      const data = await response.json();

      // Process and sort FLWs
      const flws = (data.rows || [])
        .map((row) => ({
          ...row,
          approval_rate:
            row.total_visits > 0
              ? Math.round((row.approved_visits / row.total_visits) * 100)
              : 0,
        }))
        .sort((a, b) => b.total_visits - a.total_visits);

      return {
        flws,
        summary: {
          total_flws: flws.length,
          total_visits: flws.reduce((sum, f) => sum + f.total_visits, 0),
          opportunity_id: data.opportunity_id,
          opportunity_name: data.opportunity_name || data.opportunity_id,
          metadata: data.metadata || {},
        },
      };
    },
  };

  // React hooks for common patterns
  window.hooks = {
    // Hook to fetch and manage FLW data
    useFLWData(config = {}) {
      const { useState, useEffect } = React;
      const [loading, setLoading] = useState(true);
      const [error, setError] = useState(null);
      const [data, setData] = useState(null);

      useEffect(() => {
        async function load() {
          try {
            setLoading(true);
            setError(null);
            const result = await window.labsApi.fetchFLWData(config);
            setData(result);
          } catch (err) {
            setError(err.message);
          } finally {
            setLoading(false);
          }
        }
        load();
      }, [JSON.stringify(config)]);

      return { loading, error, data };
    },
  };
})();
