from django.contrib import admin

from .models import (
    Asset,
    AssetConnection,
    AssetType,
    Equipment,
    MachinePart,
    Material,
    MaterialStockMovement,
    PreventiveMaintenancePlan,
    PreventiveMaintenanceRecord,
    PreventiveMaintenanceReminder,
    WorkOrder,
    WorkOrderChangeRequest,
    WorkOrderHistory,
    WorkOrderMaterialRequest,
)


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_location")
    list_filter = ("is_location",)
    search_fields = ("name", "key")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "asset_type", "parent", "location", "brand", "model")
    list_filter = ("asset_type",)
    search_fields = ("name", "brand", "model", "serial_number")


@admin.register(AssetConnection)
class AssetConnectionAdmin(admin.ModelAdmin):
    list_display = ("source", "kind", "target")
    list_filter = ("kind",)
    search_fields = ("source__name", "target__name")


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "location")
    search_fields = ("name", "location")


@admin.register(MachinePart)
class MachinePartAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment")
    list_filter = ("equipment",)
    search_fields = ("name", "equipment__name")


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "quantity_in_stock", "minimum_stock")
    search_fields = ("name",)


@admin.register(WorkOrderMaterialRequest)
class WorkOrderMaterialRequestAdmin(admin.ModelAdmin):
    list_display = (
        "work_order",
        "material",
        "requested_quantity",
        "delivered_quantity",
        "status",
        "requested_by",
    )
    list_filter = ("status",)
    search_fields = ("work_order__title", "material__name", "requested_by__username")


@admin.register(MaterialStockMovement)
class MaterialStockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "material", "quantity", "work_order", "actor")
    list_filter = ("kind",)
    search_fields = ("material__name", "actor__username", "work_order__title")
    readonly_fields = (
        "material",
        "kind",
        "quantity",
        "work_order",
        "request",
        "notes",
        "actor",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class WorkOrderHistoryInline(admin.TabularInline):
    model = WorkOrderHistory
    extra = 0
    can_delete = False
    readonly_fields = ("actor", "action", "old_values", "new_values", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "equipment", "status", "start_date", "estimated_days")
    list_filter = ("status", "equipment")
    search_fields = ("title", "equipment__name")
    inlines = (WorkOrderHistoryInline,)
    readonly_fields = (
        "title",
        "description",
        "equipment",
        "machine_part",
        "supervisors",
        "technicians",
        "start_date",
        "estimated_days",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrderChangeRequest)
class WorkOrderChangeRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "work_order", "requested_by", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "work_order",
        "requested_by",
        "proposed_values",
        "original_values",
        "reason",
        "status",
        "resolution_comment",
        "resolved_by",
        "created_at",
        "resolved_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkOrderHistory)
class WorkOrderHistoryAdmin(admin.ModelAdmin):
    list_display = ("work_order", "actor", "action", "created_at")
    readonly_fields = ("work_order", "actor", "action", "old_values", "new_values", "created_at")
    list_filter = ("action",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PreventiveMaintenancePlan)
class PreventiveMaintenancePlanAdmin(admin.ModelAdmin):
    list_display = ("name", "asset", "interval_days", "next_due_date", "is_active")
    list_filter = ("is_active", "asset__asset_type")
    search_fields = ("name", "asset__name")


@admin.register(PreventiveMaintenanceRecord)
class PreventiveMaintenanceRecordAdmin(admin.ModelAdmin):
    list_display = ("plan", "performed_on", "performed_by")
    readonly_fields = ("plan", "performed_on", "performed_by", "notes", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PreventiveMaintenanceReminder)
class PreventiveMaintenanceReminderAdmin(admin.ModelAdmin):
    list_display = ("plan", "due_date", "sent_at")
    readonly_fields = ("plan", "due_date", "sent_at", "recipients")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
