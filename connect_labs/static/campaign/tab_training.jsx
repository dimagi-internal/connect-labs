// tab_training.jsx — Training Hub (publicly accessible)
const { useState: useStateT } = React;

const SEED_VIDEOS = [
  {
    id: 'V1',
    title: 'Registering a household visit in CommCare',
    topic: 'Data collection',
    role: 'Recorder',
    lang: 'English',
    dur: '6:24',
    size: '14 MB',
    views: 3820,
    status: 'published',
    color: '#5D70D2',
  },
  {
    id: 'V2',
    title: 'Safe vaccine handling & cold chain basics',
    topic: 'Vaccination',
    role: 'Vaccinator',
    lang: 'Hausa',
    dur: '9:10',
    size: '22 MB',
    views: 5140,
    status: 'published',
    color: '#01A2A9',
  },
  {
    id: 'V3',
    title: 'Community mobilization door-to-door script',
    topic: 'Mobilization',
    role: 'Social Mobilizer',
    lang: 'English',
    dur: '4:48',
    size: '9 MB',
    views: 2670,
    status: 'published',
    color: '#9A5183',
  },
  {
    id: 'V4',
    title: 'Submitting your KYC documents',
    topic: 'Onboarding',
    role: 'All roles',
    lang: 'Hausa',
    dur: '3:32',
    size: '7 MB',
    views: 6210,
    status: 'published',
    color: '#3843D0',
  },
  {
    id: 'V5',
    title: 'Daily attendance & GPS check-in',
    topic: 'Operations',
    role: 'All roles',
    lang: 'English',
    dur: '2:55',
    size: '6 MB',
    views: 4490,
    status: 'published',
    color: '#694AAA',
  },
  {
    id: 'V6',
    title: 'Understanding your payment & rates',
    topic: 'Payments',
    role: 'All roles',
    lang: 'Yoruba',
    dur: '5:17',
    size: '11 MB',
    views: 1890,
    status: 'published',
    color: '#B5651D',
  },
  {
    id: 'V7',
    title: 'Supervisor field checklist (Round 2)',
    topic: 'Operations',
    role: 'Team Supervisor',
    lang: 'English',
    dur: '8:02',
    size: '19 MB',
    views: 0,
    status: 'draft',
    color: '#5D70D2',
  },
  {
    id: 'V8',
    title: 'Reporting adverse events (AEFI)',
    topic: 'Vaccination',
    role: 'Vaccinator',
    lang: 'Hausa',
    dur: '7:21',
    size: '17 MB',
    views: 0,
    status: 'archived',
    color: '#01A2A9',
  },
];

function TrainingHub({ role }) {
  const toast = useToast();
  const RBAC = window.CUT_RBAC;
  const [videos, setVideos] = useStateT(SEED_VIDEOS.map((v) => ({ ...v })));
  const [q, setQ] = useStateT('');
  const [topic, setTopic] = useStateT('all');
  const [lang, setLang] = useStateT('all');
  const [roleF, setRoleF] = useStateT('all');
  const [statusF, setStatusF] = useStateT('all');
  const [lowBw, setLowBw] = useStateT(false);
  const [upload, setUpload] = useStateT(false);
  const canManage = RBAC.can(role, 'users', 'manage'); // campaign administrators manage content

  const topics = [...new Set(SEED_VIDEOS.map((v) => v.topic))];
  const langs = [...new Set(SEED_VIDEOS.map((v) => v.lang))];
  const roles = [...new Set(SEED_VIDEOS.map((v) => v.role))];

  let list = videos.filter(
    (v) =>
      (canManage
        ? statusF === 'all' || v.status === statusF
        : v.status === 'published') &&
      (topic === 'all' || v.topic === topic) &&
      (lang === 'all' || v.lang === lang) &&
      (roleF === 'all' || v.role === roleF) &&
      (!q || v.title.toLowerCase().includes(q.toLowerCase())),
  );
  const totalViews = videos.reduce((a, v) => a + v.views, 0);
  const setStatus = (id, s) => {
    setVideos((vs) => vs.map((v) => (v.id === id ? { ...v, status: s } : v)));
    toast(
      s === 'published'
        ? 'Content published'
        : s === 'archived'
        ? 'Content archived'
        : 'Saved as draft',
    );
  };

  return (
    <Page max={1280}>
      <PageHead
        eyebrow="Resources"
        title="Training Hub"
        sub="Training videos and learning materials for campaign workers — optimized for low-bandwidth field environments."
        actions={
          canManage ? (
            <Button icon="upload" onClick={() => setUpload(true)}>
              Upload content
            </Button>
          ) : null
        }
      />

      {/* public banner */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '12px 18px',
          background: '#D9ECD4',
          borderRadius: 10,
          marginBottom: 18,
        }}
      >
        <i
          className="fa fa-globe"
          style={{ color: '#1E7B33', fontSize: 16 }}
        ></i>
        <div style={{ fontSize: 13, color: '#1E4F12' }}>
          <strong>Publicly accessible.</strong> Workers and prospective workers
          can watch these materials without logging in.
        </div>
        <div style={{ flex: 1 }}></div>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12.5,
            color: '#1E4F12',
            cursor: 'pointer',
            fontWeight: 500,
          }}
        >
          <Check checked={lowBw} onChange={setLowBw} /> Low-bandwidth mode
        </label>
      </div>

      {canManage && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 14,
            marginBottom: 18,
          }}
        >
          <Stat
            label="Published"
            value={videos.filter((v) => v.status === 'published').length}
            icon="circle-play"
          />
          <Stat
            label="Drafts"
            value={videos.filter((v) => v.status === 'draft').length}
            icon="pen-to-square"
          />
          <Stat
            label="Total views"
            value={window.CUT_DATA.num(totalViews)}
            icon="eye"
            delta="+12% this week"
            deltaTone="success"
          />
          <Stat label="Languages" value={langs.length} icon="language" />
        </div>
      )}

      {/* filters */}
      <div
        style={{
          display: 'flex',
          gap: 10,
          marginBottom: 18,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search training materials…"
            style={{
              fontFamily: 'inherit',
              fontSize: 13,
              padding: '9px 12px 9px 32px',
              border: '1px solid ' + CUTC.border,
              borderRadius: 8,
              width: '100%',
              boxSizing: 'border-box',
              outline: 'none',
              color: CUTC.purple,
            }}
          />
          <i
            className="fa fa-search"
            style={{
              position: 'absolute',
              left: 12,
              top: '50%',
              transform: 'translateY(-50%)',
              fontSize: 12,
              color: CUTC.muted,
            }}
          ></i>
        </div>
        <Select
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          style={{ width: 'auto', fontSize: 13, padding: '9px 30px 9px 12px' }}
        >
          <option value="all">All topics</option>
          {topics.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </Select>
        <Select
          value={roleF}
          onChange={(e) => setRoleF(e.target.value)}
          style={{ width: 'auto', fontSize: 13, padding: '9px 30px 9px 12px' }}
        >
          <option value="all">All roles</option>
          {roles.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </Select>
        <Select
          value={lang}
          onChange={(e) => setLang(e.target.value)}
          style={{ width: 'auto', fontSize: 13, padding: '9px 30px 9px 12px' }}
        >
          <option value="all">All languages</option>
          {langs.map((x) => (
            <option key={x}>{x}</option>
          ))}
        </Select>
        {canManage && (
          <Select
            value={statusF}
            onChange={(e) => setStatusF(e.target.value)}
            style={{
              width: 'auto',
              fontSize: 13,
              padding: '9px 30px 9px 12px',
            }}
          >
            <option value="all">All statuses</option>
            <option value="published">Published</option>
            <option value="draft">Draft</option>
            <option value="archived">Archived</option>
          </Select>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 18,
        }}
      >
        {list.map((v) => (
          <Card
            key={v.id}
            padding={0}
            hoverable
            style={{
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {/* thumbnail */}
            <div
              style={{
                position: 'relative',
                aspectRatio: '16/9',
                background: `linear-gradient(135deg, ${v.color}, ${v.color}cc)`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {lowBw ? (
                <div
                  style={{ color: '#fff', textAlign: 'center', opacity: 0.9 }}
                >
                  <i className="fa fa-image" style={{ fontSize: 22 }}></i>
                  <div style={{ fontSize: 11, marginTop: 6 }}>
                    Thumbnail hidden · low-bandwidth
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    width: 52,
                    height: 52,
                    borderRadius: '50%',
                    background: 'rgba(255,255,255,.92)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <i
                    className="fa fa-play"
                    style={{ color: v.color, fontSize: 18, marginLeft: 3 }}
                  ></i>
                </div>
              )}
              <span
                style={{
                  position: 'absolute',
                  bottom: 8,
                  right: 8,
                  background: 'rgba(0,0,0,.6)',
                  color: '#fff',
                  fontSize: 11,
                  fontWeight: 600,
                  padding: '2px 7px',
                  borderRadius: 5,
                  fontFamily: 'ui-monospace, monospace',
                }}
              >
                {v.dur}
              </span>
              {v.status !== 'published' && (
                <span style={{ position: 'absolute', top: 8, left: 8 }}>
                  <Badge tone={v.status === 'draft' ? 'warning' : 'neutral'}>
                    {v.status === 'draft' ? 'Draft' : 'Archived'}
                  </Badge>
                </span>
              )}
            </div>
            <div
              style={{
                padding: '14px 16px',
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <div
                style={{
                  fontSize: 14.5,
                  fontWeight: 600,
                  color: CUTC.purple,
                  lineHeight: 1.35,
                  textWrap: 'pretty',
                }}
              >
                {v.title}
              </div>
              <div
                style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 6,
                  marginTop: 10,
                }}
              >
                <Badge tone="primary">{v.topic}</Badge>
                <Badge tone="neutral">{v.lang}</Badge>
              </div>
              <div style={{ flex: 1 }}></div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginTop: 14,
                  fontSize: 12,
                  color: CUTC.muted,
                }}
              >
                <span>{v.role}</span>
                <span>
                  <i className="fa fa-eye" style={{ marginRight: 5 }}></i>
                  {window.CUT_DATA.num(v.views)} · {v.size}
                </span>
              </div>
              {canManage && (
                <div
                  style={{
                    display: 'flex',
                    gap: 8,
                    marginTop: 12,
                    paddingTop: 12,
                    borderTop: '1px solid ' + CUTC.borderSoft,
                  }}
                >
                  {v.status !== 'published' && (
                    <Button
                      size="sm"
                      variant="success"
                      icon="upload"
                      onClick={() => setStatus(v.id, 'published')}
                    >
                      Publish
                    </Button>
                  )}
                  {v.status === 'published' && (
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="box-archive"
                      onClick={() => setStatus(v.id, 'archived')}
                    >
                      Archive
                    </Button>
                  )}
                  {v.status === 'archived' && (
                    <Button
                      size="sm"
                      variant="secondary"
                      icon="rotate-left"
                      onClick={() => setStatus(v.id, 'draft')}
                    >
                      Restore
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" icon="pen">
                    Edit
                  </Button>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
      {list.length === 0 && (
        <Empty
          icon="graduation-cap"
          title="No training content found"
          sub="Try a different topic, role or language filter."
        />
      )}

      <UploadVideoModal
        open={upload}
        onClose={() => setUpload(false)}
        topics={topics}
        langs={langs}
        roles={roles}
        onUpload={(v) => {
          setVideos((vs) => [
            {
              id: 'V' + (vs.length + 1),
              views: 0,
              status: 'draft',
              size: '— MB',
              color: '#5D70D2',
              ...v,
            },
            ...vs,
          ]);
          toast('Uploaded — saved as draft');
          setUpload(false);
        }}
      />
    </Page>
  );
}

function UploadVideoModal({ open, onClose, topics, langs, roles, onUpload }) {
  const [title, setTitle] = useStateT('');
  const [topic, setTopic] = useStateT(topics[0]);
  const [lang, setLang] = useStateT(langs[0]);
  const [r, setR] = useStateT(roles[0]);
  const [dur, setDur] = useStateT('');
  React.useEffect(() => {
    if (open) {
      setTitle('');
      setDur('');
      setTopic(topics[0]);
      setLang(langs[0]);
      setR(roles[0]);
    }
  }, [open]);
  const valid = title.trim().length > 2;
  return (
    <Modal
      open={open}
      onClose={onClose}
      width={560}
      title="Upload training content"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            icon="upload"
            disabled={!valid}
            onClick={() =>
              onUpload({ title, topic, lang, role: r, dur: dur || '0:00' })
            }
          >
            Upload as draft
          </Button>
        </>
      }
    >
      <div
        style={{
          border: '2px dashed ' + CUTC.border,
          borderRadius: 12,
          padding: '28px 24px',
          textAlign: 'center',
          background: CUTC.surface,
          marginBottom: 16,
        }}
      >
        <i
          className="fa fa-film"
          style={{ fontSize: 26, color: 'var(--accent)' }}
        ></i>
        <div
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            color: CUTC.purple,
            marginTop: 10,
          }}
        >
          Drop a video file or click to browse
        </div>
        <div style={{ fontSize: 12, color: CUTC.muted, marginTop: 4 }}>
          MP4 / WebM · compressed automatically for low-bandwidth delivery
        </div>
      </div>
      <Field label="Title">
        <TextInput
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Safe vaccine handling basics"
        />
      </Field>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <Field label="Topic">
          <Select value={topic} onChange={(e) => setTopic(e.target.value)}>
            {topics.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </Field>
        <Field label="Worker role">
          <Select value={r} onChange={(e) => setR(e.target.value)}>
            {roles.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </Field>
        <Field label="Language">
          <Select value={lang} onChange={(e) => setLang(e.target.value)}>
            {langs.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </Field>
        <Field label="Duration">
          <TextInput
            value={dur}
            onChange={(e) => setDur(e.target.value)}
            placeholder="e.g. 5:30"
          />
        </Field>
      </div>
    </Modal>
  );
}

Object.assign(window, { TrainingHub });
