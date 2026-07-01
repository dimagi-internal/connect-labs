'use client';

/**
 * Pipeline Editor - UI for editing pipeline schemas and previewing data.
 *
 * Features:
 * - Schema editor for field mappings, aggregations, grouping
 * - Data preview table showing extracted data
 * - AI chat integration for schema modifications
 * - Works standalone or embedded in workflow UI
 */

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import {
  Save,
  X,
  RefreshCw,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Database,
  MessageSquare,
  AlertCircle,
  Check,
  Code,
  Copy,
} from 'lucide-react';

// Import AIChat component
import AIChat from '../../../components/AIChat';

// Types
interface PipelineField {
  name: string;
  path?: string;
  paths?: string[];
  aggregation?: string;
  transform?: string;
  description?: string;
  default?: unknown;
}

interface PipelineSchema {
  name?: string;
  description?: string;
  version?: number;
  grouping_key?: string;
  terminal_stage?: string;
  linking_field?: string;
  fields?: PipelineField[];
  histograms?: unknown[];
  filters?: Record<string, unknown>;
}

interface PipelineDefinition {
  id: number;
  name: string;
  description: string;
  version: number;
  schema: PipelineSchema;
  is_shared: boolean;
  shared_scope: string;
}

interface PreviewMetadata {
  row_count?: number;
  total_rows?: number;
  from_cache?: boolean;
  preview_limited?: boolean;
  error?: string;
  pipeline_name?: string;
  terminal_stage?: string;
}

interface PreviewData {
  rows: Record<string, unknown>[];
  metadata: PreviewMetadata;
}

interface PipelineEditorProps {
  definitionId: number;
  opportunityId: number;
  initialDefinition: PipelineDefinition;
  initialPreviewData?: PreviewData;
  isEmbedded?: boolean;
  onClose?: () => void;
  onSave?: () => void;
  apiEndpoints: {
    getDefinition: string;
    updateSchema: string;
    preview: string;
    sqlPreview?: string;
    chatHistory: string;
    chatClear: string;
  };
}

interface SqlPreviewData {
  terminal_stage: string;
  visit_extraction_sql: string;
  flw_aggregation_sql: string | null;
  computed_fields: string[];
  field_expressions: Record<
    string,
    {
      paths: string[];
      extraction_sql: string;
      transformed_sql: string;
      aggregation: string;
    }
  >;
}

// Aggregation options
const AGGREGATION_OPTIONS = [
  { value: 'first', label: 'First value' },
  { value: 'last', label: 'Last value' },
  { value: 'sum', label: 'Sum' },
  { value: 'avg', label: 'Average' },
  { value: 'count', label: 'Count' },
  { value: 'min', label: 'Minimum' },
  { value: 'max', label: 'Maximum' },
  { value: 'list', label: 'List all' },
  { value: 'count_unique', label: 'Count unique' },
];

// Transform options
const TRANSFORM_OPTIONS = [
  { value: '', label: 'None' },
  { value: 'float', label: 'Float' },
  { value: 'int', label: 'Integer' },
  { value: 'string', label: 'String' },
  { value: 'date', label: 'Date' },
  { value: 'kg_to_g', label: 'kg to grams' },
];

// Grouping key options
const GROUPING_OPTIONS = [
  { value: 'username', label: 'By User (username)' },
  { value: 'entity_id', label: 'By Entity (entity_id)' },
  { value: 'deliver_unit_id', label: 'By Delivery Unit' },
];

// Terminal stage options
const TERMINAL_STAGE_OPTIONS = [
  { value: 'visit_level', label: 'Visit Level (one row per form)' },
  { value: 'aggregated', label: 'Aggregated (one row per group)' },
];

function getCSRFToken(): string {
  const cookie = document.cookie
    .split('; ')
    .find((row) => row.startsWith('csrftoken='));
  return cookie ? cookie.split('=')[1] : '';
}

// Field Editor Component
function FieldEditor({
  field,
  index,
  onUpdate,
  onDelete,
  isExpanded,
  onToggle,
}: {
  field: PipelineField;
  index: number;
  onUpdate: (index: number, field: PipelineField) => void;
  onDelete: (index: number) => void;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 bg-gray-50 cursor-pointer hover:bg-gray-100"
        onClick={onToggle}
      >
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronDown size={16} className="text-gray-400" />
          ) : (
            <ChevronRight size={16} className="text-gray-400" />
          )}
          <span className="font-medium text-sm text-gray-900">
            {field.name || 'Unnamed Field'}
          </span>
          <span className="text-xs text-gray-500">
            {field.aggregation || 'first'}
          </span>
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete(index);
          }}
          className="p-1 text-gray-400 hover:text-red-600 transition-colors"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="p-3 space-y-3 bg-white">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Field Name
              </label>
              <input
                type="text"
                value={field.name}
                onChange={(e) =>
                  onUpdate(index, { ...field, name: e.target.value })
                }
                className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                placeholder="field_name"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Aggregation
              </label>
              <select
                value={field.aggregation || 'first'}
                onChange={(e) =>
                  onUpdate(index, { ...field, aggregation: e.target.value })
                }
                className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              >
                {AGGREGATION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              JSON Path
            </label>
            <input
              type="text"
              value={field.path || ''}
              onChange={(e) =>
                onUpdate(index, { ...field, path: e.target.value })
              }
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:border-blue-500 font-mono"
              placeholder="form.path.to.field"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Transform
              </label>
              <select
                value={field.transform || ''}
                onChange={(e) =>
                  onUpdate(index, {
                    ...field,
                    transform: e.target.value || undefined,
                  })
                }
                className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
              >
                {TRANSFORM_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">
                Description
              </label>
              <input
                type="text"
                value={field.description || ''}
                onChange={(e) =>
                  onUpdate(index, { ...field, description: e.target.value })
                }
                className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                placeholder="Optional description"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Data Preview Table Component
function DataPreviewTable({
  data,
  schema,
  isLoading,
  onRefresh,
}: {
  data: PreviewData;
  schema: PipelineSchema;
  isLoading: boolean;
  onRefresh: () => void;
}) {
  const columns = useMemo(() => {
    // Get columns from schema fields
    const fieldNames = (schema.fields || []).map((f) => f.name);

    // Add computed columns if they exist in the data
    if (data.rows.length > 0) {
      const firstRow = data.rows[0];
      if (firstRow.computed && typeof firstRow.computed === 'object') {
        Object.keys(firstRow.computed as Record<string, unknown>).forEach(
          (key) => {
            if (!fieldNames.includes(key)) {
              fieldNames.push(key);
            }
          },
        );
      }
    }

    // Add some standard columns
    const standardCols = ['username', 'visit_date', 'entity_id'];
    standardCols.forEach((col) => {
      if (
        !fieldNames.includes(col) &&
        data.rows.length > 0 &&
        col in data.rows[0]
      ) {
        fieldNames.unshift(col);
      }
    });

    return fieldNames;
  }, [schema.fields, data.rows]);

  const getCellValue = (row: Record<string, unknown>, col: string): string => {
    // Check direct property
    if (col in row) {
      const val = row[col];
      if (val === null || val === undefined) return '-';
      if (typeof val === 'object') return JSON.stringify(val);
      return String(val);
    }
    // Check computed
    if (
      row.computed &&
      typeof row.computed === 'object' &&
      col in (row.computed as Record<string, unknown>)
    ) {
      const val = (row.computed as Record<string, unknown>)[col];
      if (val === null || val === undefined) return '-';
      if (typeof val === 'object') return JSON.stringify(val);
      return String(val);
    }
    return '-';
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-2">
          <Database size={16} className="text-gray-500" />
          <span className="font-medium text-sm text-gray-700">
            Data Preview
          </span>
          {data.metadata.row_count !== undefined && (
            <span className="text-xs text-gray-500">
              ({data.metadata.row_count} rows
              {data.metadata.preview_limited && ', limited to 100'})
            </span>
          )}
          {data.metadata.from_cache && (
            <span className="text-xs px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded">
              cached
            </span>
          )}
        </div>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Error State */}
      {data.metadata.error && (
        <div className="p-4 bg-red-50 border-b border-red-200">
          <div className="flex items-center gap-2 text-red-700">
            <AlertCircle size={16} />
            <span className="text-sm">{data.metadata.error}</span>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {data.rows.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <Database size={32} className="mx-auto mb-2 opacity-50" />
            <p className="text-sm">No data to preview</p>
            <p className="text-xs mt-1">Configure fields and click Refresh</p>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                {columns.map((col) => (
                  <th
                    key={col}
                    className="px-3 py-2 text-left font-medium text-gray-600 border-b border-gray-200"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.rows.map((row, idx) => (
                <tr key={idx} className="hover:bg-gray-50">
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="px-3 py-1.5 text-gray-700 max-w-xs truncate"
                    >
                      {getCellValue(row, col)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// SQL Preview Component
function SqlPreviewPanel({
  data,
  isLoading,
  onRefresh,
  error,
}: {
  data: SqlPreviewData | null;
  isLoading: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  const [copiedSection, setCopiedSection] = useState<string | null>(null);

  const copyToClipboard = useCallback((text: string, section: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedSection(section);
      setTimeout(() => setCopiedSection(null), 2000);
    });
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-2">
          <Code size={16} className="text-gray-500" />
          <span className="font-medium text-sm text-gray-700">SQL Preview</span>
          {data?.terminal_stage && (
            <span className="text-xs px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">
              {data.terminal_stage}
            </span>
          )}
        </div>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Error State */}
      {error && (
        <div className="p-4 bg-red-50 border-b border-red-200">
          <div className="flex items-center gap-2 text-red-700">
            <AlertCircle size={16} />
            <span className="text-sm">{error}</span>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-auto p-4 space-y-4">
        {isLoading && !data ? (
          <div className="p-8 text-center text-gray-500">
            <RefreshCw size={24} className="mx-auto mb-2 animate-spin" />
            <p className="text-sm">Loading SQL preview...</p>
          </div>
        ) : !data ? (
          <div className="p-8 text-center text-gray-500">
            <Code size={32} className="mx-auto mb-2 opacity-50" />
            <p className="text-sm">No SQL preview available</p>
            <button
              onClick={onRefresh}
              className="mt-2 text-sm text-blue-600 hover:text-blue-800"
            >
              Generate SQL Preview
            </button>
          </div>
        ) : (
          <>
            {/* Visit Extraction SQL */}
            <div className="border border-gray-200 rounded-lg overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-200">
                <span className="text-sm font-medium text-gray-700">
                  Visit Extraction Query
                </span>
                <button
                  onClick={() =>
                    copyToClipboard(data.visit_extraction_sql, 'visit')
                  }
                  className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded"
                >
                  {copiedSection === 'visit' ? (
                    <>
                      <Check size={12} className="text-green-600" />
                      Copied
                    </>
                  ) : (
                    <>
                      <Copy size={12} />
                      Copy
                    </>
                  )}
                </button>
              </div>
              <pre className="p-3 text-xs font-mono bg-gray-900 text-gray-100 overflow-x-auto whitespace-pre-wrap">
                {data.visit_extraction_sql}
              </pre>
            </div>

            {/* FLW Aggregation SQL (if applicable) */}
            {data.flw_aggregation_sql && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-200">
                  <span className="text-sm font-medium text-gray-700">
                    FLW Aggregation Query
                  </span>
                  <button
                    onClick={() =>
                      copyToClipboard(data.flw_aggregation_sql!, 'flw')
                    }
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-600 hover:text-gray-800 hover:bg-gray-100 rounded"
                  >
                    {copiedSection === 'flw' ? (
                      <>
                        <Check size={12} className="text-green-600" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy size={12} />
                        Copy
                      </>
                    )}
                  </button>
                </div>
                <pre className="p-3 text-xs font-mono bg-gray-900 text-gray-100 overflow-x-auto whitespace-pre-wrap">
                  {data.flw_aggregation_sql}
                </pre>
              </div>
            )}

            {/* Field Expressions */}
            {Object.keys(data.field_expressions).length > 0 && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <div className="px-3 py-2 bg-gray-50 border-b border-gray-200">
                  <span className="text-sm font-medium text-gray-700">
                    Field Expressions
                  </span>
                </div>
                <div className="divide-y divide-gray-100">
                  {Object.entries(data.field_expressions).map(
                    ([name, expr]) => (
                      <div key={name} className="p-3">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-medium text-sm text-gray-900">
                            {name}
                          </span>
                          <span className="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-600 rounded">
                            {expr.aggregation}
                          </span>
                        </div>
                        <div className="text-xs text-gray-500 mb-1">
                          Path: {expr.paths.join(' | ')}
                        </div>
                        <pre className="text-xs font-mono bg-gray-50 p-2 rounded text-gray-700 overflow-x-auto">
                          {expr.transformed_sql}
                        </pre>
                      </div>
                    ),
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// Main Pipeline Editor Component
export function PipelineEditor({
  definitionId,
  opportunityId,
  initialDefinition,
  initialPreviewData,
  isEmbedded = false,
  onClose,
  onSave,
  apiEndpoints,
}: PipelineEditorProps) {
  // State
  const [definition, setDefinition] =
    useState<PipelineDefinition>(initialDefinition);
  const [schema, setSchema] = useState<PipelineSchema>(
    initialDefinition.schema || {},
  );
  const [previewData, setPreviewData] = useState<PreviewData>(
    initialPreviewData || { rows: [], metadata: {} },
  );
  const [expandedFields, setExpandedFields] = useState<Set<number>>(new Set());
  const [isSaving, setIsSaving] = useState(false);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  // Tab and SQL Preview state
  const [activeTab, setActiveTab] = useState<'data' | 'sql'>('data');
  const [sqlPreviewData, setSqlPreviewData] = useState<SqlPreviewData | null>(
    null,
  );
  const [isLoadingSql, setIsLoadingSql] = useState(false);
  const [sqlError, setSqlError] = useState<string | null>(null);

  const csrfToken = useMemo(() => getCSRFToken(), []);

  // Track changes
  useEffect(() => {
    const originalSchema = JSON.stringify(initialDefinition.schema);
    const currentSchema = JSON.stringify(schema);
    setHasChanges(originalSchema !== currentSchema);
  }, [schema, initialDefinition.schema]);

  // Update schema field
  const updateField = useCallback(
    (index: number, updatedField: PipelineField) => {
      setSchema((prev) => {
        const fields = [...(prev.fields || [])];
        fields[index] = updatedField;
        return { ...prev, fields };
      });
    },
    [],
  );

  // Delete field
  const deleteField = useCallback((index: number) => {
    setSchema((prev) => {
      const fields = [...(prev.fields || [])];
      fields.splice(index, 1);
      return { ...prev, fields };
    });
    setExpandedFields((prev) => {
      const next = new Set(prev);
      next.delete(index);
      return next;
    });
  }, []);

  // Add new field
  const addField = useCallback(() => {
    setSchema((prev) => {
      const fields = [...(prev.fields || [])];
      const newIndex = fields.length;
      fields.push({
        name: `field_${newIndex + 1}`,
        path: '',
        aggregation: 'first',
      });
      return { ...prev, fields };
    });
    // Expand the new field
    setExpandedFields((prev) => {
      const next = new Set(prev);
      next.add((schema.fields || []).length);
      return next;
    });
  }, [schema.fields]);

  // Toggle field expansion
  const toggleFieldExpanded = useCallback((index: number) => {
    setExpandedFields((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }, []);

  // Refresh preview
  const refreshPreview = useCallback(async () => {
    setIsLoadingPreview(true);
    setError(null);

    try {
      const response = await fetch(
        `${apiEndpoints.preview}?opportunity_id=${opportunityId}`,
        {
          headers: {
            'X-CSRFToken': csrfToken,
          },
        },
      );

      if (!response.ok) {
        throw new Error('Failed to fetch preview');
      }

      const data = await response.json();
      setPreviewData(data);
    } catch (e) {
      console.error('Preview error:', e);
      setError(String(e));
    } finally {
      setIsLoadingPreview(false);
    }
  }, [apiEndpoints.preview, opportunityId, csrfToken]);

  // Refresh SQL preview
  const refreshSqlPreview = useCallback(async () => {
    console.log(
      '[SQL Preview] refreshSqlPreview called, endpoint:',
      apiEndpoints.sqlPreview,
    );
    if (!apiEndpoints.sqlPreview) {
      console.log('[SQL Preview] No sqlPreview endpoint, returning');
      return;
    }

    setIsLoadingSql(true);
    setSqlError(null);

    const url = `${apiEndpoints.sqlPreview}?opportunity_id=${opportunityId}`;
    console.log('[SQL Preview] Fetching:', url);

    try {
      const response = await fetch(url, {
        headers: {
          'X-CSRFToken': csrfToken,
        },
        credentials: 'same-origin',
      });

      console.log('[SQL Preview] Response status:', response.status);

      if (!response.ok) {
        const text = await response.text();
        console.error('[SQL Preview] Response not ok:', text.substring(0, 200));
        throw new Error('Failed to fetch SQL preview');
      }

      const data = await response.json();
      console.log('[SQL Preview] Data received:', data.success);
      if (data.success) {
        setSqlPreviewData(data.sql_preview);
      } else {
        throw new Error(data.error || 'Failed to generate SQL');
      }
    } catch (e) {
      console.error('[SQL Preview] Error:', e);
      setSqlError(String(e));
    } finally {
      setIsLoadingSql(false);
    }
  }, [apiEndpoints.sqlPreview, opportunityId, csrfToken]);

  // Load SQL preview when switching to SQL tab
  useEffect(() => {
    if (activeTab === 'sql' && !sqlPreviewData && !isLoadingSql) {
      refreshSqlPreview();
    }
  }, [activeTab, sqlPreviewData, isLoadingSql, refreshSqlPreview]);

  // Save changes
  const handleSave = useCallback(async () => {
    setIsSaving(true);
    setError(null);

    try {
      const response = await fetch(apiEndpoints.updateSchema, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          schema: schema,
          name: schema.name || definition.name,
          description: schema.description || definition.description,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to save');
      }

      const result = await response.json();
      if (result.success) {
        setDefinition((prev) => ({
          ...prev,
          ...result.definition,
        }));
        setSaveSuccess(true);
        setHasChanges(false);
        setTimeout(() => setSaveSuccess(false), 2000);
        onSave?.();

        // Refresh preview after save
        refreshPreview();
      } else {
        throw new Error(result.error || 'Failed to save');
      }
    } catch (e) {
      console.error('Save error:', e);
      setError(String(e));
    } finally {
      setIsSaving(false);
    }
  }, [
    schema,
    definition,
    apiEndpoints.updateSchema,
    csrfToken,
    onSave,
    refreshPreview,
  ]);

  // Handle AI updates
  const handleDefinitionUpdate = useCallback(
    (newDef: Record<string, unknown>) => {
      console.log('Pipeline definition updated by AI:', newDef);

      // The AI sends the schema directly (not wrapped), so check if this looks like a schema
      // A schema has fields array and typically grouping_key
      if (newDef.fields && Array.isArray(newDef.fields)) {
        // This is the schema itself
        setSchema(newDef as unknown as PipelineSchema);
        // Trigger a save to persist the AI changes
        setHasChanges(true);
      } else if (newDef.schema) {
        // Legacy: wrapped schema format
        setSchema(newDef.schema as PipelineSchema);
        setHasChanges(true);
      }

      // Handle name/description updates
      if (newDef.name && typeof newDef.name === 'string') {
        setDefinition((prev) => ({ ...prev, name: newDef.name as string }));
      }
      if (newDef.description && typeof newDef.description === 'string') {
        setDefinition((prev) => ({
          ...prev,
          description: newDef.description as string,
        }));
      }
    },
    [],
  );

  return (
    <div
      className={`flex flex-col h-full bg-white ${
        isEmbedded ? '' : 'rounded-lg shadow-sm'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-3">
          <Database size={20} className="text-orange-600" />
          <div>
            <h2 className="font-semibold text-gray-900">
              {schema.name || definition.name || 'Pipeline Editor'}
            </h2>
            <p className="text-xs text-gray-500">
              {schema.description ||
                definition.description ||
                'Configure data extraction'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {hasChanges && (
            <span className="text-xs text-amber-600 font-medium">
              Unsaved changes
            </span>
          )}
          {saveSuccess && (
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <Check size={14} />
              Saved
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={isSaving || !hasChanges}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Save size={14} />
            {isSaving ? 'Saving...' : 'Save'}
          </button>
          {isEmbedded && onClose && (
            <button
              onClick={onClose}
              className="p-1.5 text-gray-400 hover:text-gray-600"
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-sm text-red-700 flex items-center gap-2">
          <AlertCircle size={14} />
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-auto text-red-500 hover:text-red-700"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Main Content - Split View */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Schema Editor / SQL Preview with Tabs */}
        <div className="w-1/2 border-r border-gray-200 flex flex-col overflow-hidden">
          {/* Tab Bar */}
          <div className="flex border-b border-gray-200 bg-gray-50">
            <button
              onClick={() => setActiveTab('data')}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'data'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              <div className="flex items-center gap-2">
                <Database size={14} />
                Schema
              </div>
            </button>
            {apiEndpoints.sqlPreview && (
              <button
                onClick={() => setActiveTab('sql')}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === 'sql'
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Code size={14} />
                  SQL Preview
                </div>
              </button>
            )}
          </div>

          {/* Left Pane Content */}
          {activeTab === 'data' ? (
            <div className="flex-1 overflow-auto p-4 space-y-4">
              {/* Basic Settings */}
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    Pipeline Name
                  </label>
                  <input
                    type="text"
                    value={schema.name || ''}
                    onChange={(e) =>
                      setSchema((prev) => ({ ...prev, name: e.target.value }))
                    }
                    className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="My Pipeline"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    Description
                  </label>
                  <textarea
                    value={schema.description || ''}
                    onChange={(e) =>
                      setSchema((prev) => ({
                        ...prev,
                        description: e.target.value,
                      }))
                    }
                    className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="What this pipeline extracts..."
                    rows={2}
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Grouping
                    </label>
                    <select
                      value={schema.grouping_key || 'username'}
                      onChange={(e) =>
                        setSchema((prev) => ({
                          ...prev,
                          grouping_key: e.target.value,
                        }))
                      }
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                    >
                      {GROUPING_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Output Level
                    </label>
                    <select
                      value={schema.terminal_stage || 'visit_level'}
                      onChange={(e) =>
                        setSchema((prev) => ({
                          ...prev,
                          terminal_stage: e.target.value,
                        }))
                      }
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                    >
                      {TERMINAL_STAGE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {/* Fields Section */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-medium text-gray-700">Fields</h4>
                  <button
                    onClick={addField}
                    className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded"
                  >
                    <Plus size={14} />
                    Add Field
                  </button>
                </div>
                <div className="space-y-2">
                  {(schema.fields || []).map((field, index) => (
                    <FieldEditor
                      key={index}
                      field={field}
                      index={index}
                      onUpdate={updateField}
                      onDelete={deleteField}
                      isExpanded={expandedFields.has(index)}
                      onToggle={() => toggleFieldExpanded(index)}
                    />
                  ))}
                  {(!schema.fields || schema.fields.length === 0) && (
                    <div className="p-4 text-center text-gray-500 border-2 border-dashed border-gray-200 rounded-lg">
                      <p className="text-sm">No fields configured</p>
                      <button
                        onClick={addField}
                        className="mt-2 text-sm text-blue-600 hover:text-blue-800"
                      >
                        Add your first field
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <SqlPreviewPanel
              data={sqlPreviewData}
              isLoading={isLoadingSql}
              onRefresh={refreshSqlPreview}
              error={sqlError}
            />
          )}
        </div>

        {/* Right: Data Preview */}
        <div className="w-1/2 flex flex-col overflow-hidden">
          <DataPreviewTable
            data={previewData}
            schema={schema}
            isLoading={isLoadingPreview}
            onRefresh={refreshPreview}
          />
        </div>
      </div>

      {/* AI Chat Toggle Button - Only show when NOT embedded (embedded uses workflow chat) */}
      {!isEmbedded && (
        <button
          onClick={() => setIsChatOpen(!isChatOpen)}
          className="fixed bottom-4 right-4 p-3 bg-blue-600 text-white rounded-full shadow-lg hover:bg-blue-700 transition-colors z-40"
          title="Edit with AI"
        >
          <MessageSquare size={24} />
        </button>
      )}

      {/* AI Chat Panel - Only show when NOT embedded */}
      {!isEmbedded && isChatOpen && (
        <div className="fixed right-0 top-0 h-full w-96 bg-white shadow-xl z-50 flex flex-col">
          <AIChat
            agentType="pipeline"
            definitionId={definitionId}
            opportunityId={opportunityId}
            currentDefinition={schema}
            onDefinitionUpdate={handleDefinitionUpdate}
            historyEndpoint={apiEndpoints.chatHistory}
            clearEndpoint={apiEndpoints.chatClear}
            onClose={() => setIsChatOpen(false)}
            title="Pipeline AI Editor"
            placeholder="Describe changes to the pipeline schema..."
            examplePrompts={[
              'Add a field for child weight',
              'Change grouping to by entity',
              'Add aggregation for visit count',
            ]}
          />
        </div>
      )}
    </div>
  );
}

// Mount function for standalone page
export function mountPipelineEditor(elementId: string) {
  const container = document.getElementById(elementId);
  if (!container) {
    console.error(`Element ${elementId} not found`);
    return;
  }

  const dataScript = document.getElementById('pipeline-data');
  if (!dataScript) {
    console.error('Pipeline data script not found');
    return;
  }

  try {
    const data = JSON.parse(dataScript.textContent || '{}');

    const React = require('react');
    const { createRoot } = require('react-dom/client');

    const root = createRoot(container);
    root.render(
      <PipelineEditor
        definitionId={data.definition_id}
        opportunityId={data.opportunity_id}
        initialDefinition={{
          id: data.definition_id,
          name: data.definition?.name || '',
          description: data.definition?.description || '',
          version: data.definition?.version || 1,
          schema: data.definition?.schema || {},
          is_shared: data.definition?.is_shared || false,
          shared_scope: data.definition?.shared_scope || 'global',
        }}
        initialPreviewData={data.preview_data}
        apiEndpoints={data.apiEndpoints}
      />,
    );
  } catch (e) {
    console.error('Failed to mount pipeline editor:', e);
  }
}

// Auto-mount on page load
if (typeof window !== 'undefined') {
  window.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('pipeline-editor-root');
    if (root) {
      mountPipelineEditor('pipeline-editor-root');
    }
  });
}

export default PipelineEditor;
