<template>
  <div class="restocking">
    <div class="page-header">
      <h2>Restocking</h2>
      <p>Set a budget and we'll recommend items to restock based on the largest stock gaps from the demand forecast.</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <!-- Budget slider -->
      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">Available Budget</h3>
        </div>
        <div class="budget-body">
          <div class="budget-value">{{ currencySymbol }}{{ budget.toLocaleString() }}</div>
          <input
            type="range"
            class="budget-slider"
            :min="0"
            :max="sliderMax"
            :step="500"
            v-model.number="budget"
          />
          <div class="budget-scale">
            <span>{{ currencySymbol }}0</span>
            <span>{{ currencySymbol }}{{ sliderMax.toLocaleString() }}</span>
          </div>
          <div class="budget-meta">
            <div>
              <span class="meta-label">Selected</span>
              <span class="meta-value">{{ currencySymbol }}{{ selectedTotal.toLocaleString() }}</span>
            </div>
            <div>
              <span class="meta-label">Remaining</span>
              <span class="meta-value" :class="{ negative: remaining < 0 }">
                {{ currencySymbol }}{{ remaining.toLocaleString() }}
              </span>
            </div>
            <div>
              <span class="meta-label">Items</span>
              <span class="meta-value">{{ selectedItems.length }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Recommendations -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">
            Recommended Items
            <span class="subtitle">ranked by stock gap (forecast − current)</span>
          </h3>
        </div>
        <div class="table-container">
          <table class="rec-table">
            <thead>
              <tr>
                <th class="col-check"></th>
                <th>SKU</th>
                <th>Item</th>
                <th>Category</th>
                <th class="num">Forecast</th>
                <th class="num">In Stock</th>
                <th class="num">Gap</th>
                <th class="num">Qty</th>
                <th class="num">Unit Cost</th>
                <th class="num">Line Cost</th>
                <th class="num">Lead Time</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="rec in recommendations"
                :key="rec.item_sku"
                :class="{ selected: selected.has(rec.item_sku), zerogap: rec.recommended_qty === 0 }"
              >
                <td class="col-check">
                  <input
                    type="checkbox"
                    :checked="selected.has(rec.item_sku)"
                    :disabled="rec.recommended_qty === 0"
                    @change="toggle(rec.item_sku)"
                  />
                </td>
                <td><code>{{ rec.item_sku }}</code></td>
                <td>{{ rec.item_name }}</td>
                <td>{{ rec.category }}</td>
                <td class="num">{{ rec.forecasted_demand }}</td>
                <td class="num">{{ rec.current_stock }}</td>
                <td class="num">
                  <span :class="['gap', rec.stock_gap > 0 ? 'short' : 'ok']">
                    {{ rec.stock_gap > 0 ? '+' : '' }}{{ rec.stock_gap }}
                  </span>
                </td>
                <td class="num">{{ rec.recommended_qty }}</td>
                <td class="num">{{ currencySymbol }}{{ rec.unit_cost.toFixed(2) }}</td>
                <td class="num"><strong>{{ currencySymbol }}{{ rec.line_cost.toLocaleString() }}</strong></td>
                <td class="num">{{ rec.lead_time_days }}d</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="actions">
          <div v-if="lastOrder" class="success-msg">
            Order <strong>{{ lastOrder.order_number }}</strong> submitted —
            expected delivery in {{ lastOrder.lead_time_days }} days.
            <router-link to="/orders">View in Orders →</router-link>
          </div>
          <button
            class="btn-primary"
            :disabled="selectedItems.length === 0 || remaining < 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? 'Submitting…' : 'Place Order' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()
    const currencySymbol = computed(() => (currentCurrency.value === 'JPY' ? '¥' : '$'))

    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const recommendations = ref([])
    const budget = ref(25000)
    const selected = ref(new Set())
    const lastOrder = ref(null)

    const sliderMax = computed(() => {
      const total = recommendations.value.reduce((s, r) => s + r.line_cost, 0)
      return Math.max(10000, Math.ceil(total / 1000) * 1000)
    })

    const selectedItems = computed(() =>
      recommendations.value.filter(r => selected.value.has(r.item_sku))
    )

    const selectedTotal = computed(() =>
      Math.round(selectedItems.value.reduce((s, r) => s + r.line_cost, 0) * 100) / 100
    )

    const remaining = computed(() =>
      Math.round((budget.value - selectedTotal.value) * 100) / 100
    )

    // Greedy auto-select by stock gap to fill the budget. Recomputed whenever
    // the slider moves so manual toggles afterward override per-item.
    const autoSelect = () => {
      const next = new Set()
      let spent = 0
      for (const r of recommendations.value) {
        if (r.recommended_qty === 0) continue
        if (spent + r.line_cost <= budget.value) {
          next.add(r.item_sku)
          spent += r.line_cost
        }
      }
      selected.value = next
    }

    const toggle = (sku) => {
      const next = new Set(selected.value)
      next.has(sku) ? next.delete(sku) : next.add(sku)
      selected.value = next
    }

    const loadRecommendations = async () => {
      try {
        loading.value = true
        error.value = null
        recommendations.value = await api.getRestockingRecommendations()
        autoSelect()
      } catch (err) {
        error.value = 'Failed to load recommendations: ' + err.message
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      try {
        submitting.value = true
        const items = selectedItems.value.map(r => ({
          item_sku: r.item_sku,
          item_name: r.item_name,
          category: r.category,
          quantity: r.recommended_qty,
          unit_cost: r.unit_cost,
          line_cost: r.line_cost,
          lead_time_days: r.lead_time_days,
        }))
        lastOrder.value = await api.submitRestockingOrder({ budget: budget.value, items })
        selected.value = new Set()
      } catch (err) {
        error.value = 'Failed to submit order: ' + err.message
      } finally {
        submitting.value = false
      }
    }

    watch(budget, autoSelect)
    onMounted(loadRecommendations)

    return {
      t,
      currencySymbol,
      loading,
      error,
      submitting,
      recommendations,
      budget,
      sliderMax,
      selected,
      selectedItems,
      selectedTotal,
      remaining,
      lastOrder,
      toggle,
      placeOrder,
    }
  },
}
</script>

<style scoped>
.budget-card .budget-body {
  padding: 1.5rem;
}

.budget-value {
  font-size: 2rem;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 0.75rem;
}

.budget-slider {
  width: 100%;
  accent-color: #3b82f6;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: #64748b;
  margin-top: 0.25rem;
}

.budget-meta {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-top: 1.25rem;
  padding-top: 1.25rem;
  border-top: 1px solid #e2e8f0;
}

.meta-label {
  display: block;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #64748b;
}

.meta-value {
  display: block;
  font-size: 1.125rem;
  font-weight: 600;
  color: #0f172a;
}

.meta-value.negative {
  color: #ef4444;
}

.card-title .subtitle {
  font-size: 0.8rem;
  font-weight: 400;
  color: #64748b;
  margin-left: 0.5rem;
}

.rec-table {
  width: 100%;
}

.rec-table .num {
  text-align: right;
}

.rec-table .col-check {
  width: 40px;
  text-align: center;
}

.rec-table tbody tr.selected {
  background: #eff6ff;
}

.rec-table tbody tr.zerogap {
  opacity: 0.5;
}

.rec-table code {
  font-size: 0.8rem;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}

.gap {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 0.8rem;
}

.gap.short {
  background: #fef3c7;
  color: #92400e;
}

.gap.ok {
  background: #dcfce7;
  color: #166534;
}

.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.5rem;
  border-top: 1px solid #e2e8f0;
  gap: 1rem;
}

.btn-primary {
  background: #0f172a;
  color: #fff;
  border: none;
  padding: 0.75rem 1.5rem;
  font-size: 0.9rem;
  font-weight: 500;
  border-radius: 6px;
  cursor: pointer;
}

.btn-primary:hover:not(:disabled) {
  background: #1e293b;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.success-msg {
  font-size: 0.875rem;
  color: #166534;
  background: #dcfce7;
  padding: 0.625rem 1rem;
  border-radius: 6px;
}

.success-msg a {
  color: #166534;
  font-weight: 500;
  text-decoration: underline;
  margin-left: 0.5rem;
}
</style>
