"""
OCS Bulk Outreach Workflow Template.

Create tasks and initiate AI chatbot conversations for multiple workers at once.
"""

DEFINITION = {
    "name": "OCS Bulk Outreach",
    "description": "Create tasks and initiate AI chatbot conversations for multiple workers at once",
    "version": 1,
    "templateType": "ocs_outreach",
    "defaultPrompt": "Please reach out to this field worker to discuss their recent performance and offer support.",
    "taskTitleTemplate": "AI Outreach: {name}",
    "statuses": [
        {"id": "pending", "label": "Not Started", "color": "gray"},
        {"id": "outreach_initiated", "label": "Outreach Initiated", "color": "blue"},
        {"id": "in_progress", "label": "In Progress", "color": "yellow"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
    // OCS State
    const [ocsConnected, setOcsConnected] = React.useState(false);
    const [ocsLoading, setOcsLoading] = React.useState(true);
    const [ocsLoginUrl, setOcsLoginUrl] = React.useState('/labs/ocs/initiate/');
    const [bots, setBots] = React.useState([]);
    const [selectedBot, setSelectedBot] = React.useState('');
    const [promptText, setPromptText] = React.useState(definition.defaultPrompt || '');

    // Worker Selection
    const [selectedWorkers, setSelectedWorkers] = React.useState({});
    const [selectAll, setSelectAll] = React.useState(false);

    // Progress Tracking
    const [isRunning, setIsRunning] = React.useState(false);
    const [progress, setProgress] = React.useState({});

    const workerStates = instance.state?.worker_states || {};

    // Check OCS status on mount
    React.useEffect(() => {
        setOcsLoading(true);
        actions.checkOCSStatus().then(status => {
            setOcsConnected(status.connected);
            setOcsLoginUrl(status.login_url || '/labs/ocs/initiate/');
            if (status.connected) {
                actions.listOCSBots().then(result => {
                    if (result.success) {
                        setBots(result.bots || []);
                        if (result.bots?.length > 0) {
                            setSelectedBot(result.bots[0].id);
                        }
                    }
                    setOcsLoading(false);
                });
            } else {
                setOcsLoading(false);
            }
        }).catch(() => setOcsLoading(false));
    }, []);

    const toggleWorker = (username) => {
        setSelectedWorkers(prev => ({ ...prev, [username]: !prev[username] }));
    };

    const handleSelectAll = () => {
        const newState = !selectAll;
        setSelectAll(newState);
        const newSelected = {};
        workers.forEach(w => { newSelected[w.username] = newState; });
        setSelectedWorkers(newSelected);
    };

    const selectedCount = Object.values(selectedWorkers).filter(Boolean).length;

    const handleCreateTasks = async () => {
        const workersToProcess = workers.filter(w => selectedWorkers[w.username]);
        if (workersToProcess.length === 0) return;

        setIsRunning(true);

        for (const worker of workersToProcess) {
            setProgress(prev => ({ ...prev, [worker.username]: { status: 'creating' } }));

            try {
                const title = (definition.taskTitleTemplate || 'Outreach: {name}')
                    .replace('{name}', worker.name || worker.username);

                const result = await actions.createTaskWithOCS({
                    username: worker.username,
                    title: title,
                    description: promptText,
                    priority: 'medium',
                    ocs: selectedBot ? { experiment: selectedBot, prompt_text: promptText } : undefined
                });

                if (result.success) {
                    const newStatus = selectedBot && result.ocs?.success ? 'ocs_initiated' : 'task_created';
                    setProgress(prev => ({
                        ...prev,
                        [worker.username]: { status: newStatus, task_id: result.task_id }
                    }));

                    await onUpdateState({
                        worker_states: {
                            ...instance.state?.worker_states,
                            [worker.username]: { task_id: result.task_id, status: 'outreach_initiated' }
                        }
                    });
                } else {
                    setProgress(prev => ({
                        ...prev,
                        [worker.username]: { status: 'error', error: result.error || 'Unknown error' }
                    }));
                }
            } catch (err) {
                setProgress(prev => ({
                    ...prev,
                    [worker.username]: { status: 'error', error: err.message || 'Unknown error' }
                }));
            }
        }

        setIsRunning(false);
    };

    const getProgressBadge = (username) => {
        const p = progress[username];
        if (!p) return null;

        const badges = {
            creating: { bg: 'bg-blue-100', text: 'text-blue-800', icon: 'fa-spinner fa-spin', label: 'Creating...' },
            task_created: { bg: 'bg-green-100', text: 'text-green-800', icon: 'fa-check', label: 'Task Created' },
            ocs_initiated: { bg: 'bg-purple-100', text: 'text-purple-800', icon: 'fa-robot', label: 'OCS Initiated' },
            error: { bg: 'bg-red-100', text: 'text-red-800', icon: 'fa-exclamation-triangle', label: 'Error' }
        };

        const badge = badges[p.status];
        if (!badge) return null;

        return (
            <span className={'inline-flex items-center gap-1 px-2 py-1 rounded ' +
                'text-xs font-medium ' + badge.bg + ' ' + badge.text}>
                <i className={'fa-solid ' + badge.icon}></i>
                {badge.label}
            </span>
        );
    };

    return (
        <div className="space-y-6">
            <div className="bg-white rounded-lg shadow-sm p-6">
                <h1 className="text-2xl font-bold text-gray-900">{definition.name}</h1>
                <p className="text-gray-600 mt-1">{definition.description}</p>
            </div>

            {ocsLoading ? (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-gray-600">
                        <i className="fa-solid fa-spinner fa-spin"></i>
                        Checking OCS connection...
                    </div>
                </div>
            ) : !ocsConnected ? (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2 text-yellow-800">
                            <i className="fa-solid fa-exclamation-triangle"></i>
                            <span>Open Chat Studio not connected.</span>
                        </div>
                        <a
                            href={ocsLoginUrl}
                            className={'px-4 py-2 bg-yellow-600 text-white rounded-lg ' +
                                'hover:bg-yellow-700 text-sm font-medium'}
                        >
                            Connect to OCS
                        </a>
                    </div>
                </div>
            ) : (
                <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                    <div className="flex items-center gap-2 text-green-800">
                        <i className="fa-solid fa-check-circle"></i>
                        <span>Connected to Open Chat Studio - {bots.length} bot(s) available</span>
                    </div>
                </div>
            )}

            <div className="bg-white rounded-lg shadow-sm p-6 space-y-4">
                <h2 className="text-lg font-semibold text-gray-900">Configuration</h2>
                {ocsConnected && bots.length > 0 && (
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            Select AI Bot
                        </label>
                        <select
                            value={selectedBot}
                            onChange={e => setSelectedBot(e.target.value)}
                            className="w-full border border-gray-300 rounded-lg px-3 py-2"
                            disabled={isRunning}
                        >
                            <option value="">No AI Bot (create tasks only)</option>
                            {bots.map(bot => (
                                <option key={bot.id} value={bot.id}>{bot.name}</option>
                            ))}
                        </select>
                    </div>
                )}
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                        Task Description
                    </label>
                    <textarea
                        value={promptText}
                        onChange={e => setPromptText(e.target.value)}
                        rows={4}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 resize-none"
                        disabled={isRunning}
                    />
                </div>
            </div>

            <div className="bg-white rounded-lg shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <h2 className="text-lg font-semibold text-gray-900">Select Workers</h2>
                        <span className="text-sm text-gray-500">{selectedCount} of {workers.length} selected</span>
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            onClick={handleSelectAll}
                            className="text-sm text-blue-600 hover:text-blue-800"
                            disabled={isRunning}
                        >
                            {selectAll ? 'Deselect All' : 'Select All'}
                        </button>
                        <button
                            onClick={handleCreateTasks}
                            disabled={selectedCount === 0 || isRunning || !promptText.trim()}
                            className={'px-4 py-2 bg-blue-600 text-white rounded-lg ' +
                                'hover:bg-blue-700 disabled:bg-gray-300 text-sm font-medium'}
                        >
                            {isRunning ? 'Processing...' : 'Create Tasks (' + selectedCount + ')'}
                        </button>
                    </div>
                </div>

                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase w-12">
                                <input
                                    type="checkbox"
                                    checked={selectAll}
                                    onChange={handleSelectAll}
                                    disabled={isRunning}
                                    className="rounded border-gray-300"
                                />
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Worker
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Visits
                            </th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                                Status
                            </th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {workers.map(worker => (
                            <tr
                                key={worker.username}
                                className={selectedWorkers[worker.username]
                                    ? 'bg-blue-50'
                                    : 'hover:bg-gray-50'}
                            >
                                <td className="px-6 py-4">
                                    <input
                                        type="checkbox"
                                        checked={selectedWorkers[worker.username] || false}
                                        onChange={() => toggleWorker(worker.username)}
                                        disabled={isRunning}
                                        className="rounded border-gray-300"
                                    />
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap">
                                    <div className="font-medium text-gray-900">
                                        {worker.name || worker.username}
                                    </div>
                                    <div className="text-sm text-gray-500">{worker.username}</div>
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                    {worker.visit_count || 0}
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap">
                                    {getProgressBadge(worker.username)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}"""

# Template export - this is what the registry imports
TEMPLATE = {
    "key": "ocs_outreach",
    "name": "OCS Bulk Outreach",
    "description": "Create tasks and initiate AI chatbot conversations for multiple workers",
    "icon": "fa-robot",
    "color": "orange",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,  # No pipeline for this template
}
