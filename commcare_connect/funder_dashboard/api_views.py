from django.http import JsonResponse


def api_funds_list(request):
    return JsonResponse({"funds": []})


def api_fund_detail(request, pk):
    return JsonResponse({"fund": None})
