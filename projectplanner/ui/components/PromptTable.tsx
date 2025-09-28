import { DndContext, DragEndEvent, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useCallback } from "react";
import type { PromptStep } from "@/lib/api";

export type PromptTableProps = {
  steps: PromptStep[];
  onStepsChange: (steps: PromptStep[]) => void;
};

export function PromptTable({ steps, onStepsChange }: PromptTableProps) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const oldIndex = steps.findIndex((step) => step.id === active.id);
      const newIndex = steps.findIndex((step) => step.id === over.id);
      const reordered = arrayMove(steps, oldIndex, newIndex);
      onStepsChange(reordered);
    },
    [steps, onStepsChange],
  );

  const updateStep = useCallback(
    (id: string, patch: Partial<PromptStep>) => {
      const updated = steps.map((step) => (step.id === id ? { ...step, ...patch } : step));
      onStepsChange(updated);
    },
    [steps, onStepsChange],
  );

  const copyPrompts = useCallback((step: PromptStep) => {
    if (typeof navigator === "undefined" || !navigator.clipboard) {
      console.warn("Clipboard API unavailable in this environment");
      return;
    }
    const content = `System:\n${step.system_prompt}\n\nUser:\n${step.user_prompt}`;
    navigator.clipboard.writeText(content).catch(() => {
      console.warn("Clipboard copy failed for", step.id);
    });
  }, []);

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800">
      <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
        <SortableContext items={steps.map((step) => step.id)} strategy={verticalListSortingStrategy}>
          <table className="min-w-full text-sm">
            <thead className="bg-slate-900 text-left uppercase text-slate-400">
              <tr>
                <th className="px-3 py-2">Order</th>
                <th className="px-3 py-2">Title & Prompt</th>
                <th className="px-3 py-2">Token Budget</th>
                <th className="px-3 py-2">Score</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((step) => (
                <SortableRow
                  key={step.id}
                  step={step}
                  onChange={updateStep}
                  onCopy={copyPrompts}
                />
              ))}
            </tbody>
          </table>
        </SortableContext>
      </DndContext>
    </div>
  );
}

type SortableRowProps = {
  step: PromptStep;
  onChange: (id: string, patch: Partial<PromptStep>) => void;
  onCopy: (step: PromptStep) => void;
};

function SortableRow({ step, onChange, onCopy }: SortableRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: step.id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <tr ref={setNodeRef} style={style} className="border-t border-slate-800">
      <td className="px-3 py-2 align-top text-slate-400">
        <button
          type="button"
          {...attributes}
          {...listeners}
          className="rounded bg-slate-800 px-2 py-1 text-xs"
        >
          Drag
        </button>
      </td>
      <td className="px-3 py-2 align-top">
        <div className="space-y-2">
          <input
            value={step.title}
            onChange={(event) => onChange(step.id, { title: event.target.value })}
            className="w-full rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
          />
          <textarea
            value={step.user_prompt}
            onChange={(event) => onChange(step.id, { user_prompt: event.target.value })}
            className="h-24 w-full rounded border border-slate-700 bg-slate-900 p-2 text-slate-100"
          />
        </div>
      </td>
      <td className="px-3 py-2 align-top">
        <input
          type="number"
          value={step.token_budget}
          onChange={(event) => onChange(step.id, { token_budget: Number(event.target.value) })}
          className="w-24 rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
        />
      </td>
      <td className="px-3 py-2 align-top">
        <span className="rounded bg-slate-800 px-2 py-1 text-xs text-emerald-300">
          {step.rubric_score?.toFixed(2) ?? "--"}
        </span>
      </td>
      <td className="px-3 py-2 align-top space-x-2">
        <button
          type="button"
          onClick={() => onCopy(step)}
          className="rounded bg-emerald-500 px-3 py-1 text-xs font-medium text-slate-950"
        >
          Copy prompt
        </button>
      </td>
    </tr>
  );
}