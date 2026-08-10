import { useStore } from "../store";

export function PlanSwitcher() {
  const plans = useStore((s) => s.plans);
  const activePlanId = useStore((s) => s.activePlanId);
  const planName = useStore((s) => s.planName);
  const constraints = useStore((s) => s.constraints);
  const switchPlan = useStore((s) => s.switchPlan);
  const rename = useStore((s) => s.renameActivePlan);
  const createTargets = useStore((s) => s.createTargetWorkspaces);

  return (
    <section className="plan-switcher" aria-label="Plan workspaces">
      {plans.length > 1 ? (
        <div className="plan-tabs" role="tablist" aria-label="Saved plans">
          {plans.map((plan) => (
            <button key={plan.id} role="tab" aria-selected={plan.id === activePlanId}
              className={`plan-tab${plan.id === activePlanId ? " active" : ""}`} onClick={() => switchPlan(plan.id)}>
              {plan.constraints.target_term || plan.name}
            </button>
          ))}
        </div>
      ) : (
        <button className="workspace-setup" onClick={createTargets}>Set up 3 target plans</button>
      )}
      <label className="plan-name">
        <span>Plan</span>
        <input value={planName} onChange={(e) => rename(e.target.value)} aria-label="Current plan name" />
        {constraints.target_term && <em>target {constraints.target_term}</em>}
      </label>
    </section>
  );
}
