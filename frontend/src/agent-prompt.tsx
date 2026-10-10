/**
 * The exact instructions an agent is given, in blocks a company may rewrite.
 *
 * All of this used to be a literal in the backend: a company saw a greeting
 * and a persona on screen while a few hundred words of behaviour it had never
 * agreed to — what the agent may promise, when it hangs up, that it will not
 * claim to be human — were decided elsewhere and could not be read, let alone
 * changed. Someone wanting a stricter rule had no way to add one, and someone
 * whose business needs a different rule had no way to remove ours.
 *
 * Each block shows our wording until somebody presses Rewrite, at which point
 * it is copied into a field to edit. That way round on purpose: an agent that
 * has not been customised keeps getting whatever we improve the default to,
 * rather than being frozen at the version it was created with.
 */
import {useEffect, useState} from 'react';
import {FileText, Loader2, Star} from 'lucide-react';
import {Button} from './app';
import {useApp} from './app-context';
import {Section, TextInput} from './ui';
import {workspace, type Agent, type PromptBlock} from './lib/api/workspace';

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

type Preview = {instructions: string; characters: number; knowledgeCharacters: number};

export function PromptBlocks({draft, canManage, agentId, onSet}: {
  draft: Agent;
  canManage: boolean;
  agentId: string;
  onSet: (patch: Partial<Agent>) => void;
}) {
  const {toast} = useApp();
  const [blocks, setBlocks] = useState<PromptBlock[]>([]);
  const [open, setOpen] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    workspace.promptBlocks().then(body => setBlocks(body.blocks)).catch(() => {
      // A failure here costs the editor its blocks, never the page: the agent
      // still works, it just cannot be customised until this loads.
    });
  }, []);

  const written = draft.promptBlocks ?? {};
  const overridden = Boolean((draft.promptOverride ?? '').trim());

  const rewrite = (id: string, text: string) =>
    onSet({promptBlocks: {...written, [id]: text}});

  const restore = (id: string) => {
    const next = {...written};
    delete next[id];
    onSet({promptBlocks: next});
  };

  const show = async () => {
    setLoading(true);
    try {
      setPreview(await workspace.agentPrompt(agentId));
    } catch (cause) {
      toast(errorText(cause, 'Could not build the preview.'));
    } finally {
      setLoading(false);
    }
  };

  return <Section title="The exact instructions"
    help="Everything the agent is told, in plain text. Leave a part alone and the standard wording is used — these are here for when yours has to be different.">

    {overridden && <div className="notice warning small">
      You are writing the whole prompt yourself, so the parts below are not
      used. Clear it at the bottom to go back to them.
    </div>}

    {blocks.map(block => {
      const mine = written[block.id];
      const customised = typeof mine === 'string';
      const editing = open === block.id;
      return <div className="field" key={block.id}>
        <label className="field-label">
          <span>{block.title}</span>
          {customised
            ? <span className="badge success"><Star size={11}/> Your wording</span>
            : <span className="badge">Standard</span>}
        </label>
        <div className="help">{block.help}</div>

        {editing
          ? <TextInput label="" rows={7} value={customised ? mine : block.default}
              disabled={!canManage || overridden}
              onChange={value => rewrite(block.id, value)}/>
          : <pre className="prompt-text">{customised ? mine : block.default}</pre>}

        <div className="row wrap" style={{gap: 8}}>
          <button type="button" className="button outline small"
            disabled={!canManage || overridden}
            onClick={() => {
              // Pressing Rewrite on a standard block copies our wording in, so
              // editing starts from something rather than an empty box.
              if (!editing && !customised) rewrite(block.id, block.default);
              setOpen(editing ? '' : block.id);
            }}>
            {editing ? 'Done' : customised ? 'Edit' : 'Rewrite this'}
          </button>
          {customised && <button type="button" className="button outline small"
            disabled={!canManage}
            onClick={() => {restore(block.id); setOpen('')}}>
            Back to the standard wording
          </button>}
        </div>
      </div>;
    })}

    <TextInput label="Anything else it must do" rows={4} value={draft.extraRules ?? ''}
      disabled={!canManage || overridden} onChange={value => onSet({extraRules: value})}
      placeholder="Always greet in Urdu first. Never mention a competitor by name."
      help="Added at the very end, so it wins any disagreement with the parts above. Write it the way you would tell a new colleague."/>

    <details className="prompt-advanced">
      <summary>Write the whole thing myself</summary>
      <div className="stack" style={{marginTop: 12}}>
        <div className="notice warning small">
          This replaces everything above. Your documents are still added at the
          end, so you do not have to paste them in. Nothing here is checked —
          leave out the rule about not claiming to be human and it will claim
          to be human.
        </div>
        <TextInput label="" rows={12} value={draft.promptOverride ?? ''}
          disabled={!canManage} onChange={value => onSet({promptOverride: value})}
          placeholder="You are…"/>
        {overridden && <div className="row">
          <button type="button" className="button outline small" disabled={!canManage}
            onClick={() => onSet({promptOverride: ''})}>
            Go back to the parts above
          </button>
        </div>}
      </div>
    </details>

    {/* Reading it back is the only way to tell whether a rule survived: the
        documents go in the middle of this and the ordering around them is
        what makes the agent follow it at all. */}
    <div className="row wrap">
      <Button variant="outline" small onClick={() => void show()} disabled={loading}>
        {loading ? <Loader2 size={14} className="spin"/> : <FileText size={14}/>}
        {' See what the agent is actually told'}
      </Button>
      {preview && <span className="small muted">
        {preview.characters.toLocaleString()} characters,
        {' '}{preview.knowledgeCharacters.toLocaleString()} of them your documents.
      </span>}
    </div>

    {preview && <>
      <pre className="prompt-text tall">{preview.instructions}</pre>
      <div className="row">
        <button type="button" className="button outline small"
          onClick={() => setPreview(null)}>Hide</button>
      </div>
    </>}
  </Section>;
}
