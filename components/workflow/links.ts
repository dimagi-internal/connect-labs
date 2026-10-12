/**
 * The `links` prop: URL builders for audit and task creation, from the base URLs
 * the server sends. Pure, so the run page and the sandboxed page frame share it.
 */
import type { AuditUrlParams, LinkHelpers, TaskUrlParams } from './types';

/**
 * Create link helpers from base URLs.
 */
export function createLinkHelpers(baseUrls: {
  auditUrlBase: string;
  taskUrlBase: string;
}): LinkHelpers {
  return {
    auditUrl: (params: AuditUrlParams): string => {
      const urlParams = new URLSearchParams();

      if (params.username) urlParams.set('usernames', params.username);
      if (params.usernames) urlParams.set('usernames', params.usernames);
      if (params.count) urlParams.set('count', String(params.count));
      urlParams.set('audit_type', params.audit_type || 'last_n_per_flw');
      urlParams.set('granularity', params.granularity || 'per_flw');
      if (params.start_date) urlParams.set('start_date', params.start_date);
      if (params.end_date) urlParams.set('end_date', params.end_date);
      if (params.title) urlParams.set('title', params.title);
      if (params.tag) urlParams.set('tag', params.tag);
      if (params.auto_create) urlParams.set('auto_create', 'true');

      return `${baseUrls.auditUrlBase}?${urlParams.toString()}`;
    },

    taskUrl: (params: TaskUrlParams): string => {
      const urlParams = new URLSearchParams();

      if (params.username) urlParams.set('username', params.username);
      if (params.title) urlParams.set('title', params.title);
      if (params.description) urlParams.set('description', params.description);
      if (params.audit_session_id)
        urlParams.set('audit_session_id', String(params.audit_session_id));
      if (params.workflow_instance_id)
        urlParams.set(
          'workflow_instance_id',
          String(params.workflow_instance_id),
        );
      if (params.priority) urlParams.set('priority', params.priority);

      return `${baseUrls.taskUrlBase}?${urlParams.toString()}`;
    },
  };
}
